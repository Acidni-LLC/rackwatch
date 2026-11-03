"""Live smoke test for Muse Spark Open through its inference partner. Writes docs/preflight_open.json. Costs under $0.05.

Everything goes through MetaClient. What this answers, before spending on a full run: does the model take our tool
definitions, does it accept its own reasoning items replayed, do effort levels work, is the prefix cached, and can a
mixed run cross between the partner and the Meta API in both directions.

    python scripts/preflight_open.py
"""

from __future__ import annotations

import base64
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.agent.loop import portable_input  # noqa: E402
from app.agent.tools import DEFINITIONS  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.meta_client import MetaClient  # noqa: E402

EFFORTS = ["minimal", "low", "medium", "high", "xhigh", "max"]
LOOKUP = {
    "type": "function",
    "name": "cmdb_lookup",
    "description": "Look up a device in the CMDB.",
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    },
}
ASK = "Use cmdb_lookup to find SW-ACC-02, then tell me its U position."
ANSWER = json.dumps({"devices": [{"hostname": "SW-ACC-02", "u_start": 12}]})


def probe(results: dict, name: str, fn) -> None:
    started = time.perf_counter()
    try:
        results[name] = {"ok": True, **fn()}
    except Exception as error:  # a preflight records failures rather than stopping at the first one
        results[name] = {"ok": False, "error": f"{type(error).__name__}: {error}"[:500]}
    results[name]["seconds"] = round(time.perf_counter() - started, 2)
    print(
        f"{name:32} {'ok' if results[name]['ok'] else 'FAIL'}  {results[name].get('note', results[name].get('error', ''))}"
    )


def main() -> None:
    settings = get_settings(mode="live")
    if not settings.open_api_key:
        raise SystemExit(f"no API key set for {settings.open_provider}")
    client = MetaClient(settings=settings, run_id="preflight_open")
    model = settings.spark_open_model
    hosted = settings.spark_model
    out: dict = {
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "provider": settings.open_provider,
        "model": model,
        "probes": {},
    }
    p = out["probes"]

    def plain():
        r = client.responses(
            purpose="preflight_plain",
            model=model,
            input="Reply with the word ready.",
            effort="low",
            max_output_tokens=600,
            store=False,
        )
        return {
            "text": r.output_text[:60],
            "usage": vars(r.usage),
            "output_types": [i.get("type") for i in r.output],
            "note": r.output_text[:40],
        }

    def structured():
        r = client.responses(
            purpose="preflight_json",
            model=model,
            input='Return JSON only: {"ok": true}',
            effort="low",
            max_output_tokens=600,
            store=False,
            text={"format": {"type": "json_object"}},
        )
        return {"parsed": json.loads(r.output_text), "note": r.output_text[:40]}

    def tools_and_reasoning():
        messages: list = [{"role": "user", "content": ASK}]
        first = client.responses(
            purpose="preflight_tool_1",
            model=model,
            input=messages,
            tools=[LOOKUP],
            effort="low",
            max_output_tokens=1200,
            store=False,
        )
        calls = first.function_calls
        if not calls:
            raise RuntimeError(f"no function_call: {[i.get('type') for i in first.output]}")
        reasoning = [i for i in first.output if i.get("type") == "reasoning"]
        summary = [i.get("summary") for i in reasoning]
        messages += first.output
        messages.append({"type": "function_call_output", "call_id": calls[0]["call_id"], "output": ANSWER})
        second = client.responses(
            purpose="preflight_tool_2",
            model=model,
            input=messages,
            tools=[LOOKUP],
            effort="low",
            max_output_tokens=1200,
            store=False,
        )
        return {
            "args": calls[0].get("arguments"),
            "reasoning_items_replayed_ok": True,
            "reasoning_summary_returned": bool(summary and summary[0]),
            "reasoning_item_keys": sorted(reasoning[0]) if reasoning else [],
            "answer": second.output_text[:120],
            "second_usage": vars(second.usage),
            "note": f"cached={second.usage.cached_tokens} answer={second.output_text[:40]!r}",
        }

    def our_real_tools():
        r = client.responses(
            purpose="preflight_real_tools",
            model=model,
            input=[{"role": "user", "content": "List the alerts, 5 at a time, starting from the beginning."}],
            tools=DEFINITIONS,
            effort="low",
            max_output_tokens=1200,
            store=False,
        )
        return {
            "called": [c.get("name") for c in r.function_calls],
            "note": str([c.get("name") for c in r.function_calls]),
        }

    def efforts():
        accepted, rejected = [], {}
        for level in EFFORTS:
            try:
                client.responses(
                    purpose="preflight_effort",
                    model=model,
                    input="Say ok.",
                    effort=level,
                    max_output_tokens=400,
                    store=False,
                )
                accepted.append(level)
            except Exception as error:
                rejected[level] = str(error)[:160]
        return {"accepted": accepted, "rejected": rejected, "note": f"accepted {accepted}"}

    def vision():
        url = f"data:image/jpeg;base64,{base64.b64encode((ROOT / 'data/rack/sample_rack.jpg').read_bytes()).decode()}"
        r = client.responses(
            purpose="preflight_vision",
            model=model,
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_image", "image_url": url},
                        {
                            "type": "input_text",
                            "text": "What does the name plate at the top of this rack say?",
                        },
                    ],
                }
            ],
            effort="low",
            max_output_tokens=800,
            store=False,
        )
        return {"text": r.output_text[:100], "note": r.output_text[:60]}

    def cross_provider():
        """A partner turn, then a Meta turn on the same history, then the partner again: what a mixed run does."""
        messages: list = [{"role": "user", "content": ASK}]
        first = client.responses(
            purpose="preflight_mixed_1",
            model=model,
            input=messages,
            tools=[LOOKUP],
            effort="low",
            max_output_tokens=1200,
            store=False,
        )
        calls = first.function_calls
        if not calls:
            raise RuntimeError("no function_call from Spark Open")
        messages += [{**i, "_model": model} for i in first.output]
        messages.append({"type": "function_call_output", "call_id": calls[0]["call_id"], "output": ANSWER})
        second = client.responses(
            purpose="preflight_mixed_2",
            model=hosted,
            input=portable_input(messages, hosted),
            tools=[LOOKUP],
            effort="low",
            max_output_tokens=1200,
            store=False,
            include=["reasoning.encrypted_content"],
        )
        messages += [{**i, "_model": hosted} for i in second.output]
        messages.append(
            {"role": "user", "content": "Now say which rack it is in if you know, or say you don't."}
        )
        third = client.responses(
            purpose="preflight_mixed_3",
            model=model,
            input=portable_input(messages, model),
            tools=[LOOKUP],
            effort="low",
            max_output_tokens=1200,
            store=False,
        )
        return {
            "meta_after_partner": second.output_text[:80],
            "partner_after_meta": third.output_text[:80],
            "note": "both directions accepted",
        }

    probe(p, "plain", plain)
    probe(p, "json_object", structured)
    probe(p, "tools_and_reasoning_replay", tools_and_reasoning)
    probe(p, "our_tool_definitions", our_real_tools)
    probe(p, "reasoning_effort_levels", efforts)
    probe(p, "image_input", vision)
    probe(p, "cross_provider_mixed", cross_provider)

    spent = client.conn.execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(est_cost_usd), 0) AS c FROM calls WHERE run_id = 'preflight_open'"
    ).fetchone()
    out["calls"], out["cost_usd"] = spent["n"], round(spent["c"], 4)
    path = ROOT / "docs/preflight_open.json"
    text = json.dumps(out, indent=2, default=str)
    for key in (settings.api_key, settings.open_api_key):
        text = text.replace(key, "<KEY>")
    path.write_text(text, encoding="utf-8")
    print(f"\nwrote {path}; {spent['n']} calls, ${spent['c']:.4f} est")


if __name__ == "__main__":
    main()
