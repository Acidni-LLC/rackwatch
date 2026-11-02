"""Budget guard (DESIGN.md §14). Totals persist in the `calls` table and exclude replayed calls."""

from __future__ import annotations

import json
import sqlite3

from .config import Settings


class BudgetExceeded(RuntimeError):
    """A call would pass a cap. R9 is a hard stop, so the run ends here instead of re-planning."""


def token_cost(settings: Settings, tokens_in: int, tokens_cached: int, tokens_out: int) -> float:
    p = settings.prices
    uncached = max(tokens_in - tokens_cached, 0)
    return (
        uncached * p.input_per_m + tokens_cached * p.cached_per_m + tokens_out * p.output_per_m
    ) / 1_000_000


def call_cost(settings: Settings, model: str, tokens_in: int, tokens_cached: int, tokens_out: int) -> float:
    if model == settings.sam_model:
        return settings.prices.sam_per_image
    return token_cost(settings, tokens_in, tokens_cached, tokens_out)


def voice_cost(settings: Settings, duration_ms: int) -> float:
    return (duration_ms // 1000) / 3600 * settings.prices.voice_per_hour


def estimate_input_tokens(request: dict) -> int:
    """A cheap upper-ish estimate: 4 chars per token for text, a flat 1,500 per image."""
    images = 0

    def walk(node) -> int:
        nonlocal images
        if isinstance(node, str):
            return len(node)
        if isinstance(node, list):
            return sum(walk(v) for v in node)
        if isinstance(node, dict):
            if node.get("type") == "input_image":
                images += 1
                return 0
            if node.get("type") == "reasoning":
                return 0
            return sum(walk(v) for v in node.values())
        return 0

    chars = walk(request.get("input")) + walk(request.get("instructions"))
    if request.get("tools"):
        chars += len(json.dumps(request["tools"]))
    return chars // 4 + images * 1500


class BudgetGuard:
    def __init__(self, settings: Settings, conn: sqlite3.Connection) -> None:
        self.settings = settings
        self.conn = conn

    def total_spent(self) -> float:
        row = self.conn.execute(
            "SELECT COALESCE(SUM(est_cost_usd), 0) AS s FROM calls WHERE replayed = 0"
        ).fetchone()
        return float(row["s"])

    def run_spent(self, run_id: str | None) -> tuple[float, int]:
        if not run_id:
            return 0.0, 0
        row = self.conn.execute(
            "SELECT COALESCE(SUM(est_cost_usd), 0) AS s, COALESCE(SUM(tokens_in - tokens_cached + tokens_out), 0) AS t "
            "FROM calls WHERE replayed = 0 AND run_id = ?",
            (run_id,),
        ).fetchone()
        return float(row["s"]), int(row["t"])

    def check(self, run_id: str | None, model: str, request: dict) -> float:
        """Refuse before the call if the estimate would pass a cap. Returns the estimate."""
        tokens_in = estimate_input_tokens(request)
        tokens_out = int(request.get("max_output_tokens") or 0)
        estimate = call_cost(self.settings, model, tokens_in, 0, tokens_out)
        total = self.total_spent()
        if total + estimate > self.settings.budget_total:
            raise BudgetExceeded(
                f"total budget: ${total:.2f} spent + ${estimate:.2f} est > ${self.settings.budget_total:.2f}"
            )
        spent, tokens = self.run_spent(run_id)
        if run_id and spent + estimate > self.settings.budget_per_run:
            raise BudgetExceeded(
                f"run budget: ${spent:.2f} spent + ${estimate:.2f} est > ${self.settings.budget_per_run:.2f}"
            )
        if run_id and tokens + tokens_in + tokens_out > self.settings.max_tokens_per_run:
            raise BudgetExceeded(
                f"run token cap: {tokens} + {tokens_in + tokens_out} > {self.settings.max_tokens_per_run}"
            )
        return estimate
