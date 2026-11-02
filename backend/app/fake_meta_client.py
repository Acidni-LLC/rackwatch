"""An offline Meta client for tests. It shares MetaClient's guards and call log, costs $0, and sends nothing.

    client = FakeMetaClient(script=[fake_text("hello"), fake_tool_call("cmdb_lookup", {"query": "U12"})])
    client = FakeMetaClient(responder=lambda request, purpose: fake_sam([(10, 20, 300, 60)], 800, 1600))

`script` entries are served in order; a callable entry is called with (request, purpose). Usage reports
zero tokens unless `usage=` says otherwise, so budget tests can charge a fake run on purpose.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable

from . import db as dbmod
from .config import Settings
from .meta_client import MetaClient


def fake_text(text: str, usage: dict | None = None) -> dict:
    return {
        "id": "resp_fake",
        "output": [
            {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}
        ],
        "output_text": text,
        "usage": usage or {"input_tokens": 0, "output_tokens": 0},
    }


def fake_tool_call(name: str, arguments: dict, call_id: str = "call_1", usage: dict | None = None) -> dict:
    return {
        "id": "resp_fake",
        "output": [
            {"type": "function_call", "call_id": call_id, "name": name, "arguments": json.dumps(arguments)}
        ],
        "usage": usage or {"input_tokens": 0, "output_tokens": 0},
    }


def fake_tool_calls(calls: list[tuple[str, dict]], usage: dict | None = None) -> dict:
    return {
        "id": "resp_fake",
        "output": [
            {"type": "function_call", "call_id": f"call_{i}", "name": name, "arguments": json.dumps(args)}
            for i, (name, args) in enumerate(calls, 1)
        ],
        "usage": usage or {"input_tokens": 0, "output_tokens": 0},
    }


def fake_sam(boxes: list[tuple[int, int, int, int]], w: int, h: int) -> dict:
    """SAM's special-token text: <0f>0<|box;...|><|mask;...|>,1<|box;...|><|mask;...|>"""
    records = [
        f"{i}<|box;x1={x1};y1={y1};x2={x2};y2={y2};w={w};h={h}|><|mask;x=0;y=0;data=1,1,~!|>"
        for i, (x1, y1, x2, y2) in enumerate(boxes)
    ]
    return fake_text("<0f>" + ",".join(records) if records else "")


class FakeTransport:
    def __init__(self, script: list | None, responder: Callable[[dict, str], dict] | None) -> None:
        self.script = list(script or [])
        self.responder = responder
        self.requests: list[dict] = []

    def _next(self, request: dict, purpose: str) -> dict:
        self.requests.append({"purpose": purpose, **request})
        if self.responder is not None:
            return self.responder(request, purpose)
        if not self.script:
            raise AssertionError(f"FakeMetaClient script ran out at purpose={purpose}")
        entry = self.script.pop(0)
        return entry(request, purpose) if callable(entry) else entry

    def responses(self, request: dict, purpose: str, req_hash: str) -> dict:
        return {"json": self._next(request, purpose), "latency_ms": 1}

    def transcribe(self, request: dict, audio: bytes, filename: str, purpose: str, req_hash: str) -> dict:
        return {"json": self._next(request, purpose), "latency_ms": 1}


class FakeMetaClient(MetaClient):
    def __init__(
        self,
        script: list | None = None,
        responder: Callable[[dict, str], dict] | None = None,
        settings: Settings | None = None,
        conn: sqlite3.Connection | None = None,
        run_id: str | None = "run_fake",
        names_guard=None,
    ) -> None:
        self.fake = FakeTransport(script, responder)
        super().__init__(
            settings=settings or Settings(api_key="", mode="live"),
            conn=conn if conn is not None else dbmod.connect(":memory:"),
            run_id=run_id,
            transport=self.fake,
            names_guard=names_guard,
        )

    @property
    def requests(self) -> list[dict]:
        return self.fake.requests
