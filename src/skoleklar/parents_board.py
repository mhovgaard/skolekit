"""Data for the parents' board, written by code: no model calls.

The parents' board gives each child a row across the panel. The left (dark) side
is the next school day; the right side looks four school days ahead, so the board
always covers the next five school days, then lists events up to two weeks out.
This module supplies what the composed week does not carry:

- `ahead`: the look-ahead days the composed school week does not cover (the next
  school week, and a weekend day in between that has something on), written from
  the facts like the plain board;
- `later_events`: trips, parties, meetings and required deadlines after the
  look-ahead, up to two weeks out.

Every line is a board line (`skoleklar.present`): its wording, the facts it shows
and how it looks, from the facts' typed fields. See docs/parents-board.md.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from skoleklar import fact as fact_model
from skoleklar.dashboard_events import birthday_notice
from skoleklar.dashboard_rules import _fact_priority, _fit, fact_text
from skoleklar.dashboard_timing import ZONE, dashboard_focus
from skoleklar.present import line, validate_line

# School days (Monday to Friday) after the next school day that the right side shows
# in full: with the next school day on the left, the board covers five school days.
LOOKAHEAD_DAYS = 4
# The look-ahead also spans the weekend days between those school days.
AHEAD_ROWS = LOOKAHEAD_DAYS + 2
# Events and deadlines after the look-ahead are listed until this many days after
# the next school day; a frame's context must reach `CONTEXT_DAYS` ahead.
LATER_DAYS = 14
CONTEXT_DAYS = LATER_DAYS + 4
LATER_LIMIT = 6
AHEAD_LINES = 5
AHEAD_SHARED = 2
TEXT_LIMIT = 100
LATER_TEXT_LIMIT = 60
LATER_KINDS = (*fact_model.EVENT_KINDS, "deadline")


def _now(context: dict) -> datetime:
    return datetime.fromisoformat(context["now"].replace("Z", "+00:00")).astimezone(ZONE)


def lookahead_dates(hero: date) -> list[date]:
    """The `LOOKAHEAD_DAYS` school days after `hero`, skipping Saturday and Sunday.

    Thursday's board looks at Friday to Wednesday; Friday's at Monday to Thursday.
    """
    result, day = [], hero
    while len(result) < LOOKAHEAD_DAYS:
        day += timedelta(days=1)
        if day.weekday() < 5:
            result.append(day)
    return result


def lookahead_span(hero: date) -> list[date]:
    """Every calendar day from the day after `hero` to its last look-ahead school day.

    The weekend days in between show only when something is on.
    """
    last = lookahead_dates(hero)[-1]
    return [hero + timedelta(days=offset) for offset in range(1, (last - hero).days + 1)]


def _usable(fact: dict) -> bool:
    return bool(fact.get("effective_date")) and not fact.get("needs_review") and not birthday_notice(fact)


def _lines(facts: list[dict], day: date) -> list[dict]:
    """A line per distinct fact wording, most important first, as the plain board writes them.

    A weekly routine is left out, so it never takes one of a day's few lines.
    """
    grouped: dict[str, list[dict]] = {}
    for fact in sorted(facts, key=_fact_priority):
        grouped.setdefault(fact_text(fact, TEXT_LIMIT), []).append(fact)
    lines = [line(text, [fact["ref"] for fact in members], members, day=day)
             for text, members in grouped.items()]
    return sorted((entry for entry in lines if not entry["hidden"]),
                  key=lambda entry: fact_model.RANK[entry["kind"]])


def ahead_rows(context: dict, state: dict) -> list[dict]:
    """Rows for look-ahead days that the composed school week does not cover.

    `context` must reach the last look-ahead school day (`lookahead_dates`).
    """
    now = _now(context)
    hero = dashboard_focus(now).school_date
    names = [child["name"] for child in state["hero"]["children"]]
    covered = {day["date"] for day in state.get("weekdays", [])}
    facts = [fact for fact in context.get("items", []) if _usable(fact)]
    rows = []
    for value in lookahead_span(hero):
        day = value.isoformat()
        if day in covered:
            continue
        on_day = [fact for fact in facts if fact["effective_date"] == day]
        if not on_day:
            continue
        children = [{"name": name,
                     "lines": _lines([fact for fact in on_day if fact.get("child") == name], value)[:AHEAD_LINES]}
                    for name in names]
        shared = _lines([fact for fact in on_day if fact.get("child") not in names], value)[:AHEAD_SHARED]
        if any(child["lines"] for child in children) or shared:
            rows.append({"date": day, "children": children, "shared": shared})
    return rows


def _later_text(fact: dict) -> str:
    return fact_text(fact, LATER_TEXT_LIMIT)


def later_events(context: dict, state: dict) -> list[dict]:
    """Trips, parties, meetings and required deadlines after the look-ahead, two weeks out.

    The list starts the day after the last look-ahead school day. One entry per
    thing: the same title on the same date for both children, or a fact for no
    particular child, is one `Begge` entry. A title already shown from the next
    school day on, or earlier in the list, is a repeat and left out.
    """
    now = _now(context)
    hero = dashboard_focus(now).school_date
    first = lookahead_dates(hero)[-1] + timedelta(days=1)
    last = hero + timedelta(days=LATER_DAYS)
    names = [child["name"] for child in state["hero"]["children"]]
    seen = {_later_text(fact).casefold() for fact in context.get("items", []) if _usable(fact)
            and hero <= date.fromisoformat(fact["effective_date"]) < first}
    grouped: dict[tuple[date, str], dict] = {}
    for fact in sorted(context.get("items", []), key=lambda fact: str(fact.get("effective_date"))):
        if not _usable(fact):
            continue
        day = date.fromisoformat(fact["effective_date"])
        if not first <= day <= last or fact_model.kind(fact) not in LATER_KINDS:
            continue
        text = _later_text(fact)
        if text.casefold() in seen and (day, text.casefold()) not in grouped:
            continue
        if any(key[1] == text.casefold() and key[0] != day for key in grouped):
            continue
        entry = grouped.setdefault((day, text.casefold()), {"day": day, "text": text, "facts": [],
                                                            "who": set()})
        entry["facts"].append(fact)
        entry["who"].add(fact.get("child") if fact.get("child") in names else "Begge")
    result = []
    for entry in grouped.values():
        who = entry["who"]
        result.append({"date": entry["day"].isoformat(),
                       "who": next(iter(who)) if len(who) == 1 else "Begge",
                       "line": line(entry["text"], [fact["ref"] for fact in entry["facts"]], entry["facts"],
                                    day=entry["day"])})
    order = {name: index for index, name in enumerate([*names, "Begge"])}
    result.sort(key=lambda entry: (entry["date"], order[entry["who"]], entry["line"]["text"]))
    return result[:LATER_LIMIT]


def attach_lookahead(state: dict, context: dict) -> dict:
    """A copy of a frame's state with `ahead` and `later_events` for its moment.

    The weekend cover and the children's day view do not use them.
    """
    if state.get("weather") or state.get("day_view"):
        return state
    result = dict(state)
    ahead = ahead_rows(context, state)
    later = later_events(context, state)
    result.pop("ahead", None)
    result.pop("later_events", None)
    if ahead:
        result["ahead"] = ahead
    if later:
        result["later_events"] = later
    return result


def validate_parents_board(data: dict, names: list[str]) -> None:
    """Allowlist `ahead`, `later_events`, `countdown` and `device`; called by the board's own `validate`."""
    ahead = data.get("ahead", [])
    if not isinstance(ahead, list) or len(ahead) > AHEAD_ROWS:
        raise ValueError(f"at most {AHEAD_ROWS} look-ahead rows")
    for row in ahead:
        if not isinstance(row, dict) or set(row) != {"date", "children", "shared"}:
            raise ValueError("unknown or missing look-ahead fields")
        date.fromisoformat(row["date"])
        children = row["children"]
        if not isinstance(children, list) or [
                child.get("name") if isinstance(child, dict) else None for child in children] != names:
            raise ValueError("look-ahead children must match the hero")
        for child in children:
            if set(child) != {"name", "lines"} or not isinstance(child["lines"], list) \
                    or len(child["lines"]) > AHEAD_LINES:
                raise ValueError("a look-ahead child has at most five lines")
            for entry in child["lines"]:
                validate_line(entry, limit=TEXT_LIMIT)
        if not isinstance(row["shared"], list) or len(row["shared"]) > AHEAD_SHARED:
            raise ValueError("at most two shared look-ahead lines")
        for entry in row["shared"]:
            validate_line(entry, limit=TEXT_LIMIT)
    later = data.get("later_events", [])
    if not isinstance(later, list) or len(later) > LATER_LIMIT:
        raise ValueError(f"at most {LATER_LIMIT} later events")
    for entry in later:
        if not isinstance(entry, dict) or set(entry) != {"date", "who", "line"}:
            raise ValueError("unknown or missing later-event fields")
        date.fromisoformat(entry["date"])
        validate_line(entry["line"], limit=LATER_TEXT_LIMIT)
        if entry["who"] not in [*names, "Begge"] or entry["line"]["kind"] not in LATER_KINDS:
            raise ValueError("a later event belongs to a child or both and is an event or a deadline")
    if "countdown" in data:
        countdown = data["countdown"]
        if (not isinstance(countdown, dict) or set(countdown) != {"lead", "text", "flag"}
                or not all(isinstance(countdown[key], str) and 1 <= len(countdown[key]) <= 40
                           for key in ("lead", "text"))
                or type(countdown["flag"]) is not bool):
            raise ValueError("countdown carries a lead, a text and whether it is a birthday")
    if "device" in data:
        device = data["device"]
        if (not isinstance(device, dict) or set(device) != {"battery_percent"}
                or type(device["battery_percent"]) is not int or not 0 <= device["battery_percent"] <= 100):
            raise ValueError("device carries only the battery percentage")
