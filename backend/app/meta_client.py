"""The ONLY place that talks to a model API: api.meta.ai, and Fireworks for Meta's open-weight Muse Spark Open.

Every model call passes through MetaClient, which:

1. refuses payloads that still hold PII (app.pii),
2. refuses calls that would pass a budget cap (app.budget),
3. writes a row to the visible call log and the SSE stream, and
4. records or serves cassettes for replay mode (app.replay).

The API key is passed explicitly to the SDK, stays inside LiveTransport and is never logged, stored in
the call log or written to a cassette. FakeMetaClient (app.fake_meta_client) shares all of this logic.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

from . import db as dbmod
from .budget import BudgetExceeded, BudgetGuard, call_cost, voice_cost
from .config import Settings, get_settings
from .events import emit, now_iso
from .pii import UnscrubbedPayloadError, assert_scrubbed
from .replay import CassettePlayer, CassetteWriter, request_hash, request_summary, strip_encrypted

__all__ = ["BudgetExceeded", "MetaClient", "MetaResponse", "Usage", "UnscrubbedPayloadError"]

ASR_ENDPOINT = "/v1/asr/transcribe"
RESPONSES_ENDPOINT = "/v1/responses"
OPEN_ENDPOINT = "fireworks:/v1/responses"
DEFAULT_MAX_OUTPUT_TOKENS = 4000


@dataclass
class Usage:
    input_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0

    @classmethod
    def from_json(cls, usage: dict | None) -> Usage:
        usage = usage or {}
        details = usage.get("input_tokens_details") or {}
        return cls(
            input_tokens=int(usage.get("input_tokens") or 0),
            cached_tokens=int(details.get("cached_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
        )


@dataclass
class MetaResponse:
    """What callers get back. `output` holds plain dicts, ready to replay into the next request."""

    id: str
    call_id: str
    model: str
    output_text: str
    output: list[dict]
    usage: Usage
    raw: dict = field(repr=False, default_factory=dict)
    latency_ms: int = 0
    replayed: bool = False
    diverged: bool = False

    @property
    def function_calls(self) -> list[dict]:
        return [item for item in self.output if item.get("type") == "function_call"]


def text_of(response_json: dict) -> str:
    if response_json.get("output_text"):
        return response_json["output_text"]
    parts: list[str] = []
    for item in response_json.get("output") or []:
        if item.get("type") == "message":
            parts.extend(
                c.get("text", "") for c in item.get("content") or [] if c.get("type") == "output_text"
            )
    return "".join(parts)


class Transport(Protocol):
    def responses(self, request: dict, purpose: str, req_hash: str) -> dict: ...
    def transcribe(self, request: dict, audio: bytes, filename: str, purpose: str, req_hash: str) -> dict: ...


class LiveTransport:
    """Real HTTP. Returns {"json", "latency_ms", "replayed", "diverged"}."""

    def __init__(self, settings: Settings) -> None:
        if not settings.api_key:
            raise RuntimeError("MODEL_API_KEY is not set; live mode needs a key (replay does not)")
        from openai import OpenAI

        self.settings = settings
        self.sdk = OpenAI(api_key=settings.api_key, base_url=settings.base_url, max_retries=4, timeout=180)

    def responses(self, request: dict, purpose: str, req_hash: str) -> dict:
        started = time.perf_counter()
        if request.get("stream"):
            response_json = self._stream(request)
        else:
            response_json = self.sdk.responses.create(**request).model_dump(mode="json", exclude_none=True)
        return {"json": response_json, "latency_ms": int((time.perf_counter() - started) * 1000)}

    def _stream(self, request: dict) -> dict:
        """SAM streams output_text deltas; the completed event carries usage."""
        deltas: list[str] = []
        final: dict = {}
        for event in self.sdk.responses.create(**request):
            kind = getattr(event, "type", "")
            if kind == "response.output_text.delta":
                deltas.append(event.delta)
            elif kind in ("response.completed", "response.incomplete", "response.failed"):
                final = event.response.model_dump(mode="json", exclude_none=True)
        final["output_text"] = "".join(deltas)
        return final

    def transcribe(self, request: dict, audio: bytes, filename: str, purpose: str, req_hash: str) -> dict:
        import requests

        started = time.perf_counter()
        response = requests.post(
            self.settings.asr_url,
            headers={"Authorization": f"Bearer {self.settings.api_key}"},
            files={
                "request": (None, json.dumps(request), "application/json"),  # params travel as a JSON part
                "audio": (filename, audio),
            },
            timeout=300,
        )
        if response.status_code != 200:
            raise RuntimeError(f"ASR HTTP {response.status_code}: {response.text[:300]}")
        return {"json": response.json(), "latency_ms": int((time.perf_counter() - started) * 1000)}


class FireworksTransport(LiveTransport):
    """Muse Spark Open on Fireworks serverless. The same Responses shape, with two differences:
    `include` only takes logprobs there, and its reasoning items carry readable summaries, not encrypted content."""

    def __init__(self, settings: Settings) -> None:
        if not settings.fireworks_api_key:
            raise RuntimeError("FIREWORKS_API_KEY is not set; Muse Spark Open needs it")
        from openai import OpenAI

        self.settings = settings
        self.sdk = OpenAI(
            api_key=settings.fireworks_api_key,
            base_url=settings.fireworks_base_url,
            max_retries=4,
            timeout=180,
        )

    def responses(self, request: dict, purpose: str, req_hash: str) -> dict:
        return super().responses({k: v for k, v in request.items() if k != "include"}, purpose, req_hash)

    def transcribe(self, request: dict, audio: bytes, filename: str, purpose: str, req_hash: str) -> dict:
        raise RuntimeError("voice notes use Muse Voice Transcribe on the Meta API")


class RecordingTransport:
    """Live calls, each also written to a cassette."""

    def __init__(self, live: LiveTransport, writer: CassetteWriter) -> None:
        self.live = live
        self.writer = writer

    def _save(
        self, result: dict, model: str, endpoint: str, purpose: str, req_hash: str, summary: str
    ) -> dict:
        cleaned = strip_encrypted(result["json"])
        self.writer.append(
            {
                "model": model,
                "endpoint": endpoint,
                "purpose": purpose,
                "request_hash": req_hash,
                "request_summary": summary,
                "response_json": cleaned,
                "usage": cleaned.get("usage"),
                "latency_ms": result["latency_ms"],
            }
        )
        return result

    def responses(self, request: dict, purpose: str, req_hash: str) -> dict:
        result = self.live.responses(request, purpose, req_hash)
        return self._save(
            result, request["model"], RESPONSES_ENDPOINT, purpose, req_hash, request_summary(request)
        )

    def transcribe(self, request: dict, audio: bytes, filename: str, purpose: str, req_hash: str) -> dict:
        result = self.live.transcribe(request, audio, filename, purpose, req_hash)
        return self._save(
            result, request["model"], ASR_ENDPOINT, purpose, req_hash, json.dumps(request)[:300]
        )


class ReplayTransport:
    """Serves a cassette, needs no key, and re-times latency (speed 1 or 4; 0 means no waiting)."""

    def __init__(self, player: CassettePlayer, speed: float = 1.0) -> None:
        self.player = player
        self.speed = speed

    def _serve(self, model: str, purpose: str, req_hash: str) -> dict:
        entry, diverged = self.player.next(model, purpose, req_hash)
        latency = int(entry.get("latency_ms") or 0)
        if self.speed > 0:
            time.sleep(latency / 1000 / self.speed)
        return {"json": entry["response_json"], "latency_ms": latency, "replayed": True, "diverged": diverged}

    def responses(self, request: dict, purpose: str, req_hash: str) -> dict:
        return self._serve(request["model"], purpose, req_hash)

    def transcribe(self, request: dict, audio: bytes, filename: str, purpose: str, req_hash: str) -> dict:
        return self._serve(request["model"], purpose, req_hash)


class MetaClient:
    """One client per run. In record and replay modes it is bound to a named cassette."""

    def __init__(
        self,
        settings: Settings | None = None,
        conn: sqlite3.Connection | None = None,
        run_id: str | None = None,
        transport: Transport | None = None,
        cassette: str | None = None,
        replay_speed: float = 1.0,
        names_guard: re.Pattern | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.conn = conn if conn is not None else dbmod.connect(self.settings.db_path)
        self.run_id = run_id
        self.names_guard = names_guard  # the PSA contact names, when known, also block a call
        self.budget = BudgetGuard(self.settings, self.conn)
        self.writer: CassetteWriter | None = None
        self.call_ids: list[str] = []
        self.open_transport: Transport | None = None
        self.routes_open = (
            transport is None and self.settings.mode != "replay"
        )  # fakes and cassettes answer for both
        if transport is not None:
            self.transport = transport
        elif self.settings.mode == "replay":
            if not cassette:
                raise ValueError("replay mode needs a cassette name")
            self.transport = ReplayTransport(CassettePlayer(self.settings.replay_dir, cassette), replay_speed)
        elif self.settings.mode == "record":
            if not cassette:
                raise ValueError("record mode needs a cassette name")
            self.writer = CassetteWriter(self.settings.replay_dir, cassette)
            self.transport = RecordingTransport(LiveTransport(self.settings), self.writer)
            if self.settings.fireworks_api_key:
                self.open_transport = RecordingTransport(FireworksTransport(self.settings), self.writer)
        else:
            self.transport = LiveTransport(self.settings)
            if self.settings.fireworks_api_key:
                self.open_transport = FireworksTransport(self.settings)

    # ---- public API --------------------------------------------------------------------

    def responses(
        self,
        *,
        purpose: str,
        model: str,
        input: Any,
        effort: str | None = None,
        instructions: str | None = None,
        tools: list[dict] | None = None,
        max_output_tokens: int | None = None,
        **kw: Any,
    ) -> MetaResponse:
        """POST /v1/responses. `effort` becomes reasoning.effort. SAM requests stream, and callers still get text."""
        request: dict[str, Any] = {"model": model, "input": input, **kw}
        if instructions is not None:
            request["instructions"] = instructions
        if tools is not None:
            request["tools"] = tools
        if model == self.settings.sam_model:
            request.setdefault("stream", True)
        else:
            request["max_output_tokens"] = max_output_tokens or DEFAULT_MAX_OUTPUT_TOKENS
            if effort:
                request["reasoning"] = {"effort": effort}
        req_hash = request_hash(request)
        endpoint = OPEN_ENDPOINT if model == self.settings.spark_open_model else RESPONSES_ENDPOINT
        self._refuse_or_pass(purpose, model, endpoint, effort, req_hash, request)
        try:
            result = self._transport_for(model).responses(request, purpose, req_hash)
        except Exception as error:
            self._log(purpose, model, endpoint, effort, req_hash, Usage(), 0, "error", self._redact(error))
            raise
        response_json = result["json"]
        usage = Usage.from_json(response_json.get("usage"))
        text = text_of(response_json)
        call_id = self._log(
            purpose,
            model,
            endpoint,
            effort,
            req_hash,
            usage,
            result.get("latency_ms", 0),
            "ok",
            text or self._summarize_output(response_json),
            replayed=result.get("replayed", False),
            diverged=result.get("diverged", False),
        )
        return MetaResponse(
            id=str(response_json.get("id", "")),
            call_id=call_id,
            model=model,
            output_text=text,
            output=list(response_json.get("output") or []),
            usage=usage,
            raw=response_json,
            latency_ms=result.get("latency_ms", 0),
            replayed=result.get("replayed", False),
            diverged=result.get("diverged", False),
        )

    def transcribe(
        self, *, purpose: str, audio: bytes, filename: str = "note.wav", keywords: list[str] | None = None
    ) -> dict:
        """POST /v1/asr/transcribe. Audio must be WAV, mono, 16-bit, 16 or 24 kHz. Returns the parsed JSON."""
        model = self.settings.asr_model
        request: dict[str, Any] = {"mode": "PUSH_TO_TALK", "model": model, "audioEncoding": "WAV"}
        if keywords:
            request["keywords"] = keywords
        req_hash = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
        self._refuse_or_pass(
            purpose, model, ASR_ENDPOINT, None, req_hash, {"input": " ".join(keywords or []), "model": model}
        )
        try:
            result = self.transport.transcribe(request, audio, filename, purpose, req_hash)
        except Exception as error:
            self._log(purpose, model, ASR_ENDPOINT, None, req_hash, Usage(), 0, "error", self._redact(error))
            raise
        response_json = result["json"]
        cost = voice_cost(self.settings, int(response_json.get("audioDurationMs") or 0))
        self._log(
            purpose,
            model,
            ASR_ENDPOINT,
            None,
            req_hash,
            Usage(),
            result.get("latency_ms", 0),
            "ok",
            str(response_json.get("transcript", "")),
            replayed=result.get("replayed", False),
            diverged=result.get("diverged", False),
            cost_override=cost,
        )
        return response_json

    def models(self) -> list[str]:
        """GET /v1/models, used for the header's key status. Replay and fakes report nothing."""
        if not isinstance(self.transport, LiveTransport | RecordingTransport):
            return []
        live = self.transport if isinstance(self.transport, LiveTransport) else self.transport.live
        return [m.id for m in live.sdk.models.list().data]

    # ---- internals ---------------------------------------------------------------------

    def _transport_for(self, model: str) -> Transport:
        if self.routes_open and model == self.settings.spark_open_model:
            if self.open_transport is None:
                raise RuntimeError("Muse Spark Open needs FIREWORKS_API_KEY in .env")
            return self.open_transport
        return self.transport

    def _refuse_or_pass(self, purpose, model, endpoint, effort, req_hash, request) -> None:
        try:
            assert_scrubbed(request, self.names_guard)
        except UnscrubbedPayloadError as error:
            self._log(purpose, model, endpoint, effort, req_hash, Usage(), 0, "refused_pii", str(error))
            raise
        if isinstance(self.transport, ReplayTransport):
            return  # replay spends nothing, so the budget cannot refuse it
        try:
            self.budget.check(self.run_id, model, request)
        except BudgetExceeded as error:
            self._log(purpose, model, endpoint, effort, req_hash, Usage(), 0, "refused_budget", str(error))
            raise

    def _redact(self, value: Any) -> str:
        text = str(value)
        for key in (self.settings.api_key, self.settings.fireworks_api_key):
            if key:
                text = text.replace(key, "<KEY>")
        return text

    @staticmethod
    def _summarize_output(response_json: dict) -> str:
        calls = [i.get("name") for i in response_json.get("output") or [] if i.get("type") == "function_call"]
        return "tool calls: " + ", ".join(str(c) for c in calls) if calls else ""

    def _log(
        self,
        purpose: str,
        model: str,
        endpoint: str,
        effort: str | None,
        req_hash: str,
        usage: Usage,
        latency_ms: int,
        status: str,
        excerpt: str,
        replayed: bool = False,
        diverged: bool = False,
        cost_override: float | None = None,
    ) -> str:
        if cost_override is not None:
            cost = cost_override
        elif status != "ok":
            cost = 0.0
        else:
            cost = call_cost(
                self.settings, model, usage.input_tokens, usage.cached_tokens, usage.output_tokens
            )
        record = {
            "id": f"call_{uuid.uuid4().hex[:10]}",
            "run_id": self.run_id,
            "ts": now_iso(),
            "model": model,
            "endpoint": endpoint,
            "purpose": purpose,
            "effort": effort,
            "latency_ms": latency_ms,
            "tokens_in": usage.input_tokens,
            "tokens_cached": usage.cached_tokens,
            "tokens_out": usage.output_tokens,
            "est_cost_usd": round(cost, 6),
            "status": status,
            "request_digest": req_hash,
            "response_excerpt": self._redact(excerpt)[:500],
            "replayed": replayed,
            "diverged": diverged,
        }
        self.conn.execute(
            "INSERT INTO calls (id, run_id, ts, model, endpoint, purpose, effort, latency_ms, tokens_in, tokens_cached, "
            "tokens_out, est_cost_usd, status, request_digest, response_excerpt, replayed, diverged) VALUES "
            "(:id, :run_id, :ts, :model, :endpoint, :purpose, :effort, :latency_ms, :tokens_in, :tokens_cached, "
            ":tokens_out, :est_cost_usd, :status, :request_digest, :response_excerpt, :replayed, :diverged)",
            record,
        )
        self.call_ids.append(record["id"])
        emit(self.conn, self.run_id, "call", record)
        return record["id"]
