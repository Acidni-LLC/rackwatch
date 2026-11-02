"""kb_search: BM25 over the knowledge base. BM25 is word statistics, not a model, so no embeddings are involved."""

from __future__ import annotations

import json
import re
import sqlite3

from rank_bm25 import BM25Okapi

DEFINITION = {
    "type": "function",
    "name": "kb_search",
    "description": "Search the knowledge base. Each article lists approved_fixes. proposed_fix_id must be one of "
    "these fix ids, or ESCALATE if none fits.",
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {"query": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 5}},
        "required": ["query", "limit"],
    },
}

TOKEN = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    return TOKEN.findall(text.lower())


class KBIndex:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.articles = [dict(r) for r in conn.execute("SELECT * FROM kb_articles ORDER BY id")]
        corpus = [tokenize(a["title"] + " " + a["body"]) for a in self.articles]
        self.bm25 = BM25Okapi(corpus) if corpus else None

    def search(self, query: str, limit: int) -> list[dict]:
        if self.bm25 is None:
            return []
        scores = self.bm25.get_scores(tokenize(query))
        ranked = sorted(range(len(self.articles)), key=lambda i: (-scores[i], self.articles[i]["id"]))
        return [self.articles[i] for i in ranked[:limit] if scores[i] > 0] or [
            self.articles[i] for i in ranked[:1]
        ]


def _snippet(body: str, query: str, width: int = 400) -> str:
    lowered = body.lower()
    positions = [lowered.find(t) for t in tokenize(query) if lowered.find(t) >= 0]
    start = max(min(positions) - 60, 0) if positions else 0
    return body[start : start + width].strip()


def kb_search(ctx, args: dict) -> dict:
    query = str(args.get("query", ""))
    limit = max(1, min(int(args.get("limit") or 3), 5))
    if ctx.kb_index is None:
        ctx.kb_index = KBIndex(ctx.conn)
    return {
        "articles": [
            {
                "id": a["id"],
                "title": a["title"],
                "snippet": _snippet(a["body"], query),
                "approved_fixes": json.loads(a["approved_fixes"]),
            }
            for a in ctx.kb_index.search(query, limit)
        ]
    }
