"""Effort router v1 (DESIGN.md §8.5): a deterministic rule, no model.

High effort for planning, re-planning and drafting claims. Low for bulk tool turns after the first three.

The engine says which model serves a turn:
  hosted  Muse Spark 1.3 on the Meta API, every turn.
  open    Muse Spark Open, Meta's open-weight model, on Fireworks, every turn.
  mixed   Muse Spark Open for low-effort tool turns, and Muse Spark 1.3 for planning and drafting.
"""

from __future__ import annotations

from ..config import Settings

PLANNING_TURNS = 3
ENGINES = ("hosted", "open", "mixed")


def pick_effort(turn_in_attempt: int, drafted: bool, demo_strict_first_attempt: bool = False) -> str:
    if demo_strict_first_attempt:
        return "low"
    if turn_in_attempt <= PLANNING_TURNS or drafted:
        return "high"
    return "low"


def purpose_for(effort: str, attempt: int) -> str:
    if attempt > 1 and effort == "high":
        return "agent_replan"
    return "agent_plan" if effort == "high" else "agent_tool_turn"


def model_for(engine: str, effort: str, settings: Settings) -> str:
    if engine == "open" or (engine == "mixed" and effort == "low"):
        return settings.spark_open_model
    return settings.spark_model
