"""What each stage of a scan delivered, carried in the board state and the brief.

A stage that fails without stopping the scan (the day summaries, the review
checks, the weather, the days ahead …) records a `degraded` entry with a fixed
reason here, instead of only logging it. The board and the brief then say in one
line what is missing, so a board that shows less says so. See docs/board-generation.md.
"""
from __future__ import annotations

import re
from typing import Any

# The stages, in the order the board names what is missing, and the words it uses.
STAGES = {
    "composition": "enkel visning",
    "facts": "punkter fra før opdateringen",
    "review_checks": "tjek af usikre punkter",
    "day_summaries": "ugeplanens dage",
    "lookahead": "dagene frem",
    "day_view": "børnenes dag",
    "weather": "vejret",
    "battery": "batteri",
    # A missing composition cache costs a model call, not a part of the board.
    "composition_cache": "",
}
_REASON = re.compile(r"[a-z][a-z0-9_]{0,47}")


def ok() -> dict[str, str]:
    return {"status": "ok"}


def degraded(reason: str | BaseException) -> dict[str, str]:
    """A degraded entry; an exception gives only its type, never its message."""
    if isinstance(reason, BaseException):
        reason = re.sub(r"(?<!^)(?=[A-Z])", "_", type(reason).__name__).lower()
    reason = str(reason)
    return {"status": "degraded", "reason": reason if _REASON.fullmatch(reason) else "error"}


def record(state: dict[str, Any], stage: str, entry: dict[str, str]) -> dict[str, Any]:
    """A copy of `state` with `stage`'s entry; an earlier degraded entry stays."""
    if stage not in STAGES:
        raise ValueError(f"unknown stage {stage}")
    health = dict(state.get("health") or {})
    if health.get(stage, {}).get("status") != "degraded":
        health[stage] = entry
    return {**state, "health": health}


def merge(state: dict[str, Any], entries: dict[str, dict[str, str]]) -> dict[str, Any]:
    for stage, entry in entries.items():
        state = record(state, stage, entry)
    return state


def missing(health: dict[str, Any] | None) -> list[str]:
    """What the board names as missing, in a fixed order."""
    health = health or {}
    return [label for stage, label in STAGES.items()
            if label and health.get(stage, {}).get("status") == "degraded"]


# More missing parts than this are counted, so the line fits the status corner.
NAMED_PARTS = 2


def line(health: dict[str, Any] | None) -> str:
    """'Mangler: vejret, dagene frem', 'Mangler 3 dele', or '' when nothing is missing."""
    parts = missing(health)
    if len(parts) > NAMED_PARTS:
        return f"Mangler {len(parts)} dele"
    return f"Mangler: {', '.join(parts)}" if parts else ""


def validate_health(value: Any) -> None:
    if not isinstance(value, dict) or set(value) - set(STAGES):
        raise ValueError("health names known stages")
    for entry in value.values():
        if entry == ok():
            continue
        if (not isinstance(entry, dict) or set(entry) != {"status", "reason"}
                or entry["status"] != "degraded" or not isinstance(entry["reason"], str)
                or not _REASON.fullmatch(entry["reason"])):
            raise ValueError("a health entry is ok, or degraded with a fixed reason")
