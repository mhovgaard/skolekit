"""Shared Europe/Copenhagen timing policy for the dashboard focus."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo


ZONE = ZoneInfo("Europe/Copenhagen")
SCHOOL_DAY_CUTOFF = time(15, 0)
# Sunday from noon belongs to Monday: the weekend panel gives way to the school day.
WEEKEND_END = time(12, 0)


@dataclass(frozen=True)
class DashboardFocus:
    mode: str
    school_date: date


def dashboard_focus(value: datetime) -> DashboardFocus:
    """Return the left-panel mode and the school date backing its week.

    School days remain current until 15:00. Friday from 15:00, Saturday, and
    Sunday before 12:00 use the weekend panel. Sunday from 12:00 advances to
    Monday. The weekend mode still selects Monday's school week for the right
    panel and for the school panel that follows it.
    """
    if value.tzinfo is None:
        raise ValueError("dashboard timing requires a timezone")
    local = value.astimezone(ZONE)
    weekday = local.weekday()
    clock = local.timetz().replace(tzinfo=None)
    after_cutoff = clock >= SCHOOL_DAY_CUTOFF

    if weekday == 4 and after_cutoff or weekday == 5 or weekday == 6 and clock < WEEKEND_END:
        result = local.date()
        while result.weekday() != 0:
            result += timedelta(days=1)
        return DashboardFocus("weekend", result)

    result = local.date()
    if weekday == 6 or after_cutoff:
        result += timedelta(days=1)
    while result.weekday() >= 5:
        result += timedelta(days=1)
    return DashboardFocus("schoolday", result)


NEW_MESSAGE_WINDOW = timedelta(hours=24)


def new_messages_since(value: datetime) -> datetime:
    """Messages count as new for 24 hours, and all weekend long once it has begun."""
    start = weekend_started_at(value)
    recent = value.astimezone(ZONE) - NEW_MESSAGE_WINDOW
    return recent if start is None else min(recent, start)


def dashboard_weekend_dates(value: datetime) -> list[date]:
    """The weekend cover is this weekend; school-week views may include its closing weekend."""
    if value.tzinfo is None:
        raise ValueError("dashboard timing requires a timezone")
    local = value.astimezone(ZONE)
    today = local.date()
    saturday = today + timedelta(days=(5 - today.weekday()) % 7)
    result = {today} if today.weekday() == 6 else {saturday, saturday + timedelta(days=1)}
    focus = dashboard_focus(local)
    if focus.mode != "weekend":
        week_start = focus.school_date - timedelta(days=focus.school_date.weekday())
        result.update({week_start + timedelta(days=5), week_start + timedelta(days=6)})
    return sorted(result)


def weekend_started_at(value: datetime) -> datetime | None:
    """Friday 15:00 of the weekend `value` falls in, or None outside the weekend panel."""
    if dashboard_focus(value).mode != "weekend":
        return None
    local = value.astimezone(ZONE)
    friday = local.date() - timedelta(days=local.weekday() - 4)
    return datetime.combine(friday, SCHOOL_DAY_CUTOFF, ZONE)
