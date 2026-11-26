"""States for every moment the dashboard layout changes, drawn ahead by one scan.

A scan composes each school week once. The later changes (the children's view,
15:00, the weekend cover, Saturday's weather shift, Sunday noon and each new
weekend date) are derived here
from that composition without another scan or model call, so a device can be
served the right frame at any time until the data itself expires.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from skoleklar.dashboard_composition import restamp
from skoleklar.dashboard_renderer import weekend_day_title
from skoleklar.dashboard_rules import weekend_selection
from skoleklar.dashboard_timing import (
    SCHOOL_DAY_CUTOFF,
    WEEKEND_END,
    ZONE,
    dashboard_focus,
    dashboard_weekend_dates,
)
from skoleklar.day_view import change_times as day_view_change_times
from skoleklar.weather import SATURDAY_UNTIL_HOUR

TIMELINE_HOURS = 36


def _changes_on(day: date) -> list[time]:
    """Clock times on `day` at which the panel may look different.

    Besides the board's own changes, the children's day view starts and ends
    (`day_view.change_times`). A moment that changes nothing visible, such as
    when no view is attached, is merged into the frame before it.

    A school night has no midnight change: the parents' board from 20:00 already
    shows the next school day, and the children's view takes over at 06:00, so
    the board is not woken to redraw a date nobody reads. The weekend cover keeps
    midnight, where its 'i morgen' becomes today.
    """
    if day.weekday() < 5:
        board = [SCHOOL_DAY_CUTOFF]
    elif day.weekday() == 5:
        board = [time(0), time(SATURDAY_UNTIL_HOUR)]
    else:
        board = [time(0), WEEKEND_END]
    return sorted(set(board) | set(day_view_change_times(day)))


def slice_starts(now: datetime, until: datetime) -> list[datetime]:
    """`now`, then each layout change before `until`, in Copenhagen time."""
    if now.tzinfo is None:
        raise ValueError("dashboard timing requires a timezone")
    now = now.astimezone(ZONE)
    result = [now]
    day = now.date()
    while True:
        for clock in _changes_on(day):
            moment = datetime.combine(day, clock, ZONE)
            if moment >= until:
                return result
            if moment > now:
                result.append(moment)
        day += timedelta(days=1)


def school_week(value: datetime) -> date:
    """Monday of the school week the panel shows at `value`."""
    focus = dashboard_focus(value).school_date
    return focus - timedelta(days=focus.weekday())


def slice_state(state: dict, context: dict) -> dict:
    """The week's composed state as the panel shows it at the context's `now`.

    The state must come from the same school week. Weather is left for the
    caller to attach for this moment.
    """
    at = datetime.fromisoformat(context["now"])
    focus = dashboard_focus(at).school_date
    result = restamp(state, context)
    result.pop("weather", None)
    result.pop("day_weather", None)
    if result["hero"]["date"] != focus.isoformat():
        card = next((day for day in result.get("weekdays", [])
                     if day["date"] == focus.isoformat()), None)
        if card is None:
            raise ValueError("slice falls outside the composed school week")
        # The weekday row already carries the day's facts; the next scan
        # composes the fuller hero for it.
        result["hero"] = {
            "state": "I dag" if focus == at.date() else "Næste skoledag",
            "day": weekend_day_title({"date": focus.isoformat()}),
            "date": focus.isoformat(),
            "children": [dict(child, lines=list(child["lines"])) for child in card["children"]],
        }
    if context.get("items") is not None:
        # The weekend rows follow this moment's weekend dates, refilled from the facts,
        # so an activity counted as overflow moves into a row that has come free.
        result["weekend"], result["weekend_overflow"], _ = weekend_selection(context)
    elif "weekend" in result:
        allowed = {day.isoformat() for day in dashboard_weekend_dates(at)}
        result["weekend"] = [row for row in result["weekend"] if row["date"] in allowed]
    return result
