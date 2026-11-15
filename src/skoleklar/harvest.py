"""The household's harvest: every source for every child, by a policy.

Harvest policies turn presets into per-source scopes for each configured
child. Messages have a placeholder source check; restricted collection
handles their transient text outside captured source records.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from skolekit import sources
from skolekit.profile import Child
from skolekit.records import SourceCheck
from skolekit.sources import Scope

# The order the harvest reads each child's pages in; the golden files pin it.
HARVEST_ORDER = ("frontpage", "messages", "calendar", "weekly_plan", "forms")


@dataclass(frozen=True, slots=True)
class HarvestPolicy:
    mode: str = "refresh"
    message_mode: str = "off"
    message_processor: str = "disabled"
    calendar_past_weeks: int = 1
    calendar_future_weeks: int = 6
    weekly_plan_weeks: int | None = 2
    message_pages: int | None = 1
    frontpage_pages: int | None = 1
    page_safety_limit: int = 100

    @classmethod
    def for_mode(
        cls,
        mode: str,
        *,
        calendar_past_weeks: int | None = None,
        calendar_future_weeks: int | None = None,
        weekly_plan_weeks: int | None = None,
        message_pages: int | None = None,
        frontpage_pages: int | None = None,
        message_mode: str = "off",
        message_processor: str = "disabled",
    ) -> "HarvestPolicy":
        if mode not in {"initialize", "refresh", "daily", "latest"}:
            raise ValueError(f"Unsupported harvest mode: {mode}")
        defaults: dict[str, Any]
        if mode == "initialize":
            defaults = {
                "calendar_past_weeks": 0,
                "calendar_future_weeks": 0,
                "weekly_plan_weeks": None,
                "message_pages": None,
                "frontpage_pages": None,
            }
        elif mode == "latest":
            defaults = {
                "calendar_past_weeks": 0,
                "calendar_future_weeks": 0,
                "weekly_plan_weeks": 1,
                "message_pages": 1,
                "frontpage_pages": 1,
            }
        elif mode == "daily":
            defaults = {
                "calendar_past_weeks": 1,
                "calendar_future_weeks": 2,
                "weekly_plan_weeks": 2,
                "message_pages": 1,
                "frontpage_pages": 1,
            }
        else:
            defaults = {
                "calendar_past_weeks": 1,
                "calendar_future_weeks": 6,
                "weekly_plan_weeks": 2,
                "message_pages": 1,
                "frontpage_pages": 1,
            }
        overrides = {
            "calendar_past_weeks": calendar_past_weeks,
            "calendar_future_weeks": calendar_future_weeks,
            "weekly_plan_weeks": weekly_plan_weeks,
            "message_pages": message_pages,
            "frontpage_pages": frontpage_pages,
        }
        defaults.update(
            {key: value for key, value in overrides.items() if value is not None}
        )
        for key in ("calendar_past_weeks", "calendar_future_weeks"):
            if defaults[key] < 0:
                raise ValueError(f"{key.replace('_', ' ')} cannot be negative")
        for key in ("weekly_plan_weeks", "message_pages", "frontpage_pages"):
            if defaults[key] is not None and defaults[key] < 1:
                raise ValueError(f"{key.replace('_', ' ')} must be at least 1")
        if message_mode not in {"off", "events-only"}:
            raise ValueError(f"Unsupported message mode: {message_mode}")
        if message_processor not in {"disabled", "local", "zdr"}:
            raise ValueError(f"Unsupported message processor: {message_processor}")
        if message_mode != "events-only" and message_processor != "disabled":
            raise ValueError(
                "Message processors can only be enabled with events-only mode"
            )
        return cls(
            mode=mode,
            message_mode=message_mode,
            message_processor=message_processor,
            **defaults,
        )


def scope_for(policy: HarvestPolicy) -> Scope:
    """The sources' scope for a harvest policy."""
    return Scope(
        pages=policy.frontpage_pages,
        weeks_back=policy.calendar_past_weeks,
        weeks_ahead=policy.calendar_future_weeks,
        school_year=policy.mode == "initialize",
        plan_weeks=policy.weekly_plan_weeks,
        page_safety_limit=policy.page_safety_limit,
    )


def message_placeholder(child: Child, policy: HarvestPolicy) -> SourceCheck:
    """The messages check: complete and empty, since no message is ever a record."""
    return SourceCheck(
        student_id=child.student_id,
        child=child.child,
        source_type="messages",
        records=[],
        complete=True,
        metadata={
            "message_mode": policy.message_mode,
            "excluded_by_policy": policy.message_mode == "off",
            "bodies_retained": 0,
        },
        coverage={"kind": "excluded_by_policy"},
    )


def collect_all(
    client: httpx.Client,
    *,
    children: tuple[Child, ...],
    policy: HarvestPolicy | None = None,
) -> list[SourceCheck]:
    """Every source for every child; a source that fails is one incomplete check."""
    policy = policy or HarvestPolicy()
    scope = scope_for(policy)
    by_name = {source.name: source for source in sources.SOURCES}
    checks: list[SourceCheck] = []
    for child in children:
        for name in HARVEST_ORDER:
            if name != "messages" and name not in by_name:
                continue
            try:
                check = (message_placeholder(child, policy) if name == "messages"
                         else by_name[name].collect(client, child, scope))
            except Exception as error:
                check = SourceCheck(
                    student_id=child.student_id,
                    child=child.child,
                    source_type=name,
                    complete=False,
                    error=f"{type(error).__name__}: {error}",
                )
            checks.append(check)
    return checks
