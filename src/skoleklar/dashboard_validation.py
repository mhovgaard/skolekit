#!/usr/bin/env python3
"""Check a composed state against its context and complete editorial audit.

The checks are structural: every reference names a context fact, every fact is
accounted for once, a line looks as its facts say (`skoleklar.present`), a shown
fact sits on a date it covers, and every weekend activity is shown, shown as
cancelled, or counted. The wording is the model's job, so no check reads it.
"""
import argparse
import json
import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from skoleklar.dashboard_events import birthday_notice
from skoleklar.dashboard_renderer import validate
from skoleklar.dashboard_rules import _activity_event
from skoleklar.dashboard_timing import dashboard_focus, dashboard_weekend_dates
from skoleklar.present import present

_LINE = re.compile(r"(hero)\.children\[(\d)\]\.lines\[(\d+)\]"
                   r"|weekdays\[(\d)\]\.children\[(\d)\]\.lines\[(\d+)\]"
                   r"|weekdays\[(\d)\]\.shared\[(\d+)\]")
_WEEKEND = re.compile(r"weekend\[(\d+)\]")


class RuleViolation(ValueError):
    """A broken composition rule. The message is fixed text; `facts` names the
    context facts involved, as `fact-N` or `fact-N at LOCATION`, so a retry can
    correct them and a log can say which facts failed without their content."""

    def __init__(self, message, facts=()):
        super().__init__(message)
        self.facts = tuple(facts)


def _at(ref, location):
    return f"{ref} at {location}" if location else str(ref)


def _now(context):
    return datetime.fromisoformat(context["now"].replace("Z", "+00:00"))


def _span(fact):
    """The dates a fact covers, at most a fortnight."""
    start = date.fromisoformat(fact["effective_date"])
    end = date.fromisoformat(fact["end_date"]) if fact.get("end_date") else start
    return {start + timedelta(days=offset) for offset in range(min(max((end - start).days, 0), 14) + 1)}


def board_lines(state):
    """(location, day, line) for every line of the composed week, weekend rows included."""
    hero_day = date.fromisoformat(state["hero"]["date"])
    for c, child in enumerate(state["hero"]["children"]):
        for i, entry in enumerate(child["lines"]):
            yield f"hero.children[{c}].lines[{i}]", hero_day, entry
    for d, weekday in enumerate(state["weekdays"]):
        day = date.fromisoformat(weekday["date"])
        for c, child in enumerate(weekday["children"]):
            for i, entry in enumerate(child["lines"]):
                yield f"weekdays[{d}].children[{c}].lines[{i}]", day, entry
        for i, entry in enumerate(weekday["shared"]):
            yield f"weekdays[{d}].shared[{i}]", day, entry
    for r, row in enumerate(state.get("weekend", [])):
        for entry in row["lines"]:
            yield f"weekend[{r}]", date.fromisoformat(row["date"]), entry


def _validate_lines(context, state, audit):
    """References exist, each line looks as its facts say, and the audit points at its lines."""
    facts = {item["ref"]: item for item in context["items"]}
    shown = defaultdict(set)
    for location, day, entry in board_lines(state):
        unknown = [ref for ref in entry["refs"] if ref not in facts]
        if unknown:
            raise RuleViolation("a board line names an unknown fact", [_at(ref, location) for ref in unknown])
        members = [facts[ref] for ref in entry["refs"]]
        if not members and entry["kind"] != "more":
            raise RuleViolation("a board line must show at least one fact", [location])
        expected = present(members, day=day)
        if location.startswith("weekend") and expected["kind"] != "more":
            # An activity cancelled by another message in its thread is cancelled too.
            expected = present([{**fact, "cancelled": fact.get("cancelled") or entry["cancelled"]}
                                for fact in members], day=day)
        if {key: entry[key] for key in expected} != expected:
            raise RuleViolation("a board line must look as its facts say",
                                [_at(ref, location) for ref in entry["refs"]] or [location])
        shown[location] |= set(entry["refs"])
    for selection in audit["facts"]:
        location = str(selection.get("location") or "")
        if selection["decision"] == "omitted" or location == "weekend_overflow":
            continue
        if selection["ref"] not in shown.get(location, set()):
            raise RuleViolation("a displayed fact must be on the line its audit names",
                                [_at(selection["ref"], location)])
    placed = {ref for refs in shown.values() for ref in refs}
    omitted = {item["ref"] for item in audit["facts"] if item["decision"] == "omitted"}
    if placed & omitted:
        raise RuleViolation("an omitted fact must not be shown", sorted(placed & omitted))


def _validate_dates(context, state, audit):
    """The calendar's dates, and each shown fact on a date it covers."""
    now = _now(context)
    hero_date = dashboard_focus(now).school_date
    monday = hero_date - timedelta(days=hero_date.weekday())
    weekdays = [monday + timedelta(days=offset) for offset in range(5)]
    if state["hero"]["date"] != hero_date.isoformat() or [
            day["date"] for day in state["weekdays"]] != [day.isoformat() for day in weekdays]:
        raise ValueError("dashboard dates must follow the selected school week")
    weekend = set(dashboard_weekend_dates(now))
    if any(date.fromisoformat(row["date"]) not in weekend for row in state.get("weekend", [])):
        raise ValueError("weekend rows must use the current upcoming weekend dates")
    facts = {item["ref"]: item for item in context["items"]}
    for selection in audit["facts"]:
        fact = facts[selection["ref"]]
        location = str(selection.get("location") or "")
        if selection["decision"] == "omitted" or not fact.get("effective_date"):
            continue
        if location == "weekend_overflow":
            allowed = weekend
        elif location.startswith("hero"):
            allowed = {hero_date}
        elif match := re.match(r"weekdays\[(\d)\]", location):
            allowed = {weekdays[int(match[1])]}
        elif match := _WEEKEND.fullmatch(location):
            allowed = {date.fromisoformat(state["weekend"][int(match[1])]["date"])}
        else:
            raise RuleViolation("a displayed fact needs a board location", [_at(selection["ref"], location)])
        if not _span(fact) & allowed:
            raise RuleViolation("displayed fact date must match dashboard location",
                                [_at(selection["ref"], location)])


def _validate_weekend_activities(context, state, audit):
    """Every current weekend activity is shown, shown as cancelled, or counted as overflow."""
    now = _now(context)
    weekend_dates = {day.isoformat() for day in dashboard_weekend_dates(now)}
    facts = {item["ref"]: item for item in context["items"]}
    selections = {item["ref"]: item for item in audit["facts"]}
    cancelled_threads = {(fact.get("child"), fact.get("effective_date"), fact["message_thread"])
                         for fact in context["items"] if fact.get("cancelled") and fact.get("message_thread")}
    rows = state.get("weekend", [])
    events = defaultdict(list)
    for ref, fact in facts.items():
        if not (fact.get("confirmed_activity_kind") or fact.get("activity_kind")) \
                or fact.get("effective_date") not in weekend_dates:
            continue
        selection = selections[ref]
        location = str(selection.get("location") or "")
        match = _WEEKEND.fullmatch(location) if selection["decision"] != "omitted" else None
        cancelled = fact.get("cancelled") or (fact.get("child"), fact.get("effective_date"),
                                              fact.get("message_thread")) in cancelled_threads
        if match:
            entry = next(entry for entry in rows[int(match[1])]["lines"] if ref in entry["refs"])
            if cancelled and not entry["cancelled"]:
                raise RuleViolation("cancelled activity must not appear as a plan", [_at(ref, location)])
        if cancelled:
            continue
        events[_activity_event(fact)].append((int(match[1]) if match else None, selection, ref))
    by_row = defaultdict(set)
    overflow = 0
    for event, members in events.items():
        rows_shown = {row for row, _, _ in members if row is not None}
        if not rows_shown:
            if not any(selection["decision"] != "omitted" and selection.get("location") == "weekend_overflow"
                       for _, selection, _ in members):
                raise RuleViolation("confirmed weekend activity must be displayed", [ref for *_, ref in members])
            overflow += 1
        for row in rows_shown:
            by_row[row].add(event)
    for row, row_events in by_row.items():
        if len(row_events) > 1:
            raise RuleViolation("distinct weekend activities must not share a row", [f"weekend[{row}]"])
    if state.get("weekend_overflow", 0) != overflow:
        raise ValueError("weekend overflow must count the activities not shown")
    if overflow and len(rows) < 2:
        raise ValueError("weekend overflow requires both rows to be used")


def _validate_required(context, audit):
    """A classmate's birth date never shows; a shown message event shows its bring-item."""
    facts = {item["ref"]: item for item in context["items"]}
    selections = {item["ref"]: item for item in audit["facts"]}
    for ref, fact in facts.items():
        if selections[ref]["decision"] != "omitted" and birthday_notice(fact):
            raise RuleViolation("birthday notice is not a family activity",
                                [_at(ref, selections[ref].get("location"))])
    for ref, fact in facts.items():
        if not (fact.get("private_message_fact") and fact.get("type") == "bring"
                and selections[ref]["decision"] == "omitted"):
            continue
        displayed_events = [
            _at(other_ref, selections[other_ref].get("location"))
            for other_ref, other in facts.items()
            if other.get("private_message_fact") and other.get("type") == "event"
            and other.get("child") == fact.get("child")
            and other.get("effective_date") == fact.get("effective_date")
            and selections[other_ref]["decision"] != "omitted"
            and selections[other_ref].get("location") != "weekend_overflow"
        ]
        if displayed_events:
            # The child's event for that date is on the board, so its bring-item must be too.
            raise RuleViolation("private event row must include its retained bring-item",
                                [ref, *displayed_events])


def validate_composition(context, state, audit):
    validate(state)
    expected = [x["ref"] for x in context["items"]]
    actual = [x["ref"] for x in audit["facts"]]
    if len(actual) != len(set(actual)) or set(actual) != set(expected):
        raise ValueError("audit must account for each context fact exactly once")
    if any(x.get("decision") not in {"displayed", "merged", "omitted"} or not x.get("reason")
           for x in audit["facts"]):
        raise ValueError("every selection needs a decision and reason")
    for key in ("source_updated_at", "expires_at", "status"):
        if state.get("freshness", {}).get(key) != context["freshness"][key]:
            raise ValueError("state must preserve source freshness")
    if audit.get("source_updated_at") != context["freshness"]["source_updated_at"]:
        raise ValueError("audit must identify the same source update")
    for key in ("checked", "unread_count", "bodies_retained"):
        if state["messages"][key] != context["messages"][key]:
            raise ValueError("state must preserve inbox coverage and aggregate counts")
    if sorted(c["name"] for c in state["hero"]["children"]) != sorted(context["children"]):
        raise ValueError("state children must match the source context")
    _validate_lines(context, state, audit)
    if context.get("now"):
        _validate_dates(context, state, audit)
        _validate_weekend_activities(context, state, audit)
    _validate_required(context, audit)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("context", type=Path)
    parser.add_argument("state", type=Path)
    parser.add_argument("audit", type=Path)
    args = parser.parse_args()
    values = [json.loads(p.read_text()) for p in (args.context, args.state, args.audit)]
    validate_composition(*values)
    print("State, source freshness, inbox totals and complete selection audit verified")
