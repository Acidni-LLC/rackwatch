"""Effort router v1 (DESIGN.md §8.5): a deterministic rule, no model.

High effort for planning, re-planning and drafting claims. Low for bulk tool turns after the first three.
"""

from __future__ import annotations

PLANNING_TURNS = 3


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
