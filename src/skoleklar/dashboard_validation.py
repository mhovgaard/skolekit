#!/usr/bin/env python3
"""Check composed state against its context and complete editorial audit."""
import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from skoleklar.dashboard_renderer import validate
from skoleklar.dashboard_timing import dashboard_focus, dashboard_weekend_dates
from skoleklar.dashboard_events import birthday_notice


_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "maj": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "okt": 10, "nov": 11, "dec": 12,
}
_WEEKDAYS = r"mandag|tirsdag|onsdag|torsdag|fredag|lørdag|søndag"


class RuleViolation(ValueError):
    """A broken composition rule. The message is fixed text; `facts` names the
    context facts involved, as `fact-N` or `fact-N at LOCATION`, so a retry can
    correct them and a log can say which facts failed without their content."""

    def __init__(self, message, facts=()):
        super().__init__(message)
        self.facts = tuple(facts)


def _at(ref, location):
    return f"{ref} at {location}" if location else str(ref)


def _value_at_location(state, location):
    value = state
    for key, index in re.findall(r"(?:^|\.)([^.\[\]]+)|\[(\d+)\]", str(location)):
        if key:
            if not isinstance(value, dict) or key not in value:
                return ""
            value = value[key]
        else:
            if not isinstance(value, list) or int(index) >= len(value):
                return ""
            value = value[int(index)]
    if isinstance(value, str):
        return value
    return " ".join(_strings(value))


def _person_display_forms(value):
    """Full name plus deterministic first-name/all-initials abbreviations."""
    parts = [part for part in str(value).split() if part]
    if not parts:
        return set()

    def initials(part):
        return "-".join(piece[0] + "." for piece in part.split("-") if piece)

    result = {" ".join(parts).casefold()}
    if len(parts) > 1:
        result.add((parts[0] + " " + " ".join(initials(part) for part in parts[1:])).casefold())
        result.add((parts[0] + " " + initials(parts[-1])).casefold())
        if len(parts[0]) > 6:
            result.add((parts[0][:4] + ". " + " ".join(
                initials(part) for part in parts[1:]
            )).casefold())
    return result


def _display_date(value, around):
    match = re.search(r"\b(\d{1,2})\.\s*([a-zæøå]{3})\.?", str(value).casefold())
    if not match or match.group(2) not in _MONTHS:
        return None
    result = date(around.year, _MONTHS[match.group(2)], int(match.group(1)))
    if result < around - timedelta(days=180):
        result = date(around.year + 1, result.month, result.day)
    elif result > around + timedelta(days=180):
        result = date(around.year - 1, result.month, result.day)
    return result


def _weekend_dates(today):
    if today.weekday() == 6:
        result = {today}
    else:
        saturday = today + timedelta(days=(5 - today.weekday()) % 7)
        result = {saturday, saturday + timedelta(days=1)}
    return result


def _validate_temporal_placement(context, state, audit):
    if not context.get("now"):
        return
    now = datetime.fromisoformat(context["now"].replace("Z", "+00:00"))
    hero_date = dashboard_focus(now).school_date
    if _display_date(state["hero"]["day"], hero_date) != hero_date:
        raise ValueError("dashboard date labels must follow selected school week")
    if state.get("schema_version") == 2:
        week_start = hero_date - timedelta(days=hero_date.weekday())
        expected_weekdays = [week_start + timedelta(days=offset) for offset in range(5)]
        actual_weekdays = [date.fromisoformat(item["date"]) for item in state.get("weekdays", [])]
        if actual_weekdays != expected_weekdays or any(
            _display_date(item["day"], expected_weekdays[index]) != expected_weekdays[index]
            for index, item in enumerate(state.get("weekdays", []))
        ):
            raise ValueError("dashboard date labels must follow selected school week")
        lookahead_end = expected_weekdays[-1]
    else:
        expected_horizon = [hero_date + timedelta(days=offset) for offset in (1, 2, 3)]
        expected_later = [hero_date + timedelta(days=offset) for offset in (4, 5)]
        if [_display_date(item["day"], hero_date) for item in state.get("horizon", [])] != expected_horizon:
            raise ValueError("dashboard date labels must follow hero and five-day sequence")
        if [_display_date(item["day"], hero_date) for item in state.get("later", [])] != expected_later:
            raise ValueError("dashboard date labels must follow hero and five-day sequence")
        lookahead_end = expected_later[-1]
    allowed_weekend = (set(dashboard_weekend_dates(now)) if state.get("schema_version") == 2
                       else _weekend_dates(now.date()))
    if any(_display_date(item["day"], now.date()) not in allowed_weekend
           for item in state.get("weekend", [])):
        raise ValueError("weekend rows must use the current upcoming weekend dates")
    footer_date = _display_date(state.get("footer", ""), hero_date)
    if footer_date is not None and footer_date in allowed_weekend:
        raise ValueError("weekend activities must use the fixed weekend area")
    if footer_date is not None and footer_date <= lookahead_end and footer_date not in allowed_weekend:
        raise ValueError("dated footer must follow five-day lookahead")

    facts = {item["ref"]: item for item in context["items"]}
    for item in audit["facts"]:
        fact = facts.get(item["ref"], {})
        if item.get("decision") == "omitted" or not fact.get("effective_date"):
            continue
        fact_date = date.fromisoformat(fact["effective_date"])
        # A fact spanning several days may sit on any of them, at most a fortnight.
        end = date.fromisoformat(fact["end_date"]) if fact.get("end_date") else fact_date
        span = {fact_date + timedelta(days=offset)
                for offset in range(min(max((end - fact_date).days, 0), 14) + 1)}
        location = str(item.get("location") or "")
        allowed = None
        if location.startswith("hero"):
            allowed = {hero_date}
        elif location == "weekend_overflow":
            allowed = allowed_weekend
        elif location.startswith("weekend"):
            match = re.match(r"weekend\[(\d+)\]", location)
            index = int(match.group(1)) if match else -1
            allowed = ({_display_date(state["weekend"][index]["day"], now.date())}
                       if 0 <= index < len(state.get("weekend", [])) else set())
        elif state.get("schema_version") == 2:
            match = re.match(r"weekdays\[(\d+)\]", location)
            if match and int(match.group(1)) < len(expected_weekdays):
                allowed = {expected_weekdays[int(match.group(1))]}
            if location.startswith("footer"):
                allowed = (
                    {fact_date}
                    if fact_date > lookahead_end and fact_date not in allowed_weekend
                    else set()
                )
        else:
            match = re.match(r"horizon\[(\d+)\]", location)
            if match and int(match.group(1)) < len(expected_horizon):
                allowed = {expected_horizon[int(match.group(1))]}
            match = re.match(r"later\[(\d+)\]", location)
            if match and int(match.group(1)) < len(expected_later):
                allowed = {expected_later[int(match.group(1))]}
            if location.startswith("footer"):
                allowed = set()
                if fact_date > lookahead_end:
                    allowed.add(fact_date)
        if allowed is not None and not (span & allowed if not location.startswith("footer") else fact_date in allowed):
            raise RuleViolation("displayed fact date must match dashboard location",
                                [_at(item["ref"], location)])


def _weekend_row(location):
    match = re.fullmatch(r"weekend\[(\d+)\](?:\.\w+)?", str(location))
    return int(match.group(1)) if match else None


def _activity_event(fact):
    """One weekend activity, however many facts report it.

    A child has at most one class trip or class event a day. Parties and
    parent-arranged activities are told apart by their message thread, so two
    parties on one day stay two activities.
    """
    kind = fact.get("confirmed_activity_kind") or fact.get("activity_kind")
    if kind in {"class_trip", "class_event"}:
        return fact.get("child"), fact.get("effective_date"), kind
    return fact.get("child"), fact.get("effective_date"), fact.get("message_thread") or fact["ref"]


def _weekend_kinds(state, row):
    """The kinds code wrote for a weekend row, or None for a state without them."""
    rows = state.get("weekend") or []
    kinds = rows[row].get("kinds") if row is not None and 0 <= row < len(rows) else None
    return kinds or None


def _validate_weekend_activities(context, state, audit):
    """Every current weekend activity is shown, shown as cancelled, or counted as overflow."""
    if not context.get("now"):
        return
    weekend_dates = {d.isoformat() for d in dashboard_weekend_dates(
        datetime.fromisoformat(context["now"].replace("Z", "+00:00")))}
    facts = {item["ref"]: item for item in context["items"]}
    selections = {item["ref"]: item for item in audit["facts"]}
    # A cancellation in the same thread on the same date cancels the event it reports.
    cancelled_threads = {(fact.get("child"), fact.get("effective_date"), fact["message_thread"])
                         for fact in context["items"] if fact.get("cancelled") and fact.get("message_thread")}
    events = defaultdict(list)
    for ref, fact in facts.items():
        kind = fact.get("confirmed_activity_kind") or fact.get("activity_kind")
        if not kind or fact.get("effective_date") not in weekend_dates:
            continue
        selection = selections[ref]
        location = str(selection.get("location") or "")
        row = _weekend_row(location) if selection["decision"] != "omitted" else None
        visible = _value_at_location(state, location).casefold() if row is not None else ""
        if fact.get("cancelled") or (fact.get("child"), fact.get("effective_date"),
                                     fact.get("message_thread")) in cancelled_threads:
            if row is not None and "aflyst" not in visible:
                raise RuleViolation("cancelled activity must not appear as a plan", [_at(ref, location)])
            continue
        events[_activity_event(fact)].append((kind, row, visible, selection, location, ref))
    rows = defaultdict(list)
    refs_by_row = defaultdict(list)
    overflow = 0
    for event, members in events.items():
        shown = [member for member in members if member[1] is not None]
        if not shown:
            if not any(selection["decision"] != "omitted" and location == "weekend_overflow"
                       for _, _, _, selection, location, _ in members):
                raise RuleViolation("confirmed weekend activity must be displayed",
                                    [member[5] for member in members])
            overflow += 1
        for kind, row, visible, _, location, ref in shown:
            kinds = _weekend_kinds(state, row)
            if kinds is not None:
                # The row shows the activity's kind (its box), whatever its words.
                from skoleklar.parents_board import _ACTIVITY_KINDS
                if kinds[0] != _ACTIVITY_KINDS.get(kind, "event"):
                    raise RuleViolation("weekend activity must keep its kind", [_at(ref, location)])
                continue
            # A state without kinds: the row's words must say it, as before.
            if kind == "class_trip" and not re.search(r"\bklassetur\b|\btur\b.*\bklassen\b", visible):
                raise RuleViolation("confirmed class trip must retain its activity kind",
                                    [_at(ref, location)])
            if kind == "birthday_party" and "fødselsdag" not in visible:
                raise RuleViolation("birthday party must retain its activity kind",
                                    [_at(ref, location)])
            if event not in rows[row]:
                rows[row].append(event)
            refs_by_row[row].append(_at(ref, location))
    for row, members in rows.items():
        children = [event[0] for event in members]
        if len(children) != len(set(children)):
            raise RuleViolation("distinct weekend activities must not share a row", refs_by_row[row])
    if state.get("weekend_overflow", 0) != overflow:
        raise ValueError("weekend overflow must count the activities not shown")
    if overflow:
        if len(state.get("weekend", [])) < 2:
            raise ValueError("weekend overflow requires both rows to be used")
        for ref, fact in facts.items():
            row = _weekend_row(selections[ref].get("location"))
            if birthday_notice(fact) and selections[ref]["decision"] != "omitted" and row is not None and row not in rows:
                raise ValueError("birthday notice cannot take a row from an activity")


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
    _validate_temporal_placement(context, state, audit)
    sections = [
        state["hero"],
        *(state.get("weekdays", []) if state.get("schema_version") == 2 else state.get("horizon", [])),
    ]
    if any(str(child["heading"]).strip().casefold() == str(child["name"]).strip().casefold()
           for section in sections for child in section["children"]):
        raise ValueError("child heading must describe status and not repeat child name")
    if any(
        re.search(rf"\b({_WEEKDAYS})\b", str(child["heading"]).casefold())
        or str(child["heading"]).strip().casefold() in {"husk", "til dagen", "dagens punkter"}
        for section in sections for child in section["children"]
    ):
        raise ValueError("child heading must describe content rather than repeat date")
    if any("aktivitet registreret" in str(item.get("text", "")).casefold()
           for item in state.get("weekend", [])):
        raise ValueError("weekend activity must state source and action without filler")
    facts = {item["ref"]: item for item in context["items"]}
    selections = {item["ref"]: item for item in audit["facts"]}
    for ref, fact in facts.items():
        selection = selections[ref]
        location = str(selection.get("location", ""))
        if selection["decision"] != "omitted" and birthday_notice(fact):
            # A classmate's birth date is not the child's event, so it never displays.
            raise RuleViolation("birthday notice is not a family activity", [_at(ref, location)])
        kind = fact.get("confirmed_activity_kind") or fact.get("activity_kind")
        if kind and selection["decision"] != "omitted" and _weekend_row(location) is not None:
            from skoleklar.parents_board import EVENT_KINDS
            kinds = _weekend_kinds(state, _weekend_row(location))
            if (kinds[0] not in EVENT_KINDS if kinds is not None
                    else "besked" in _value_at_location(state, location).casefold()):
                raise RuleViolation("weekend activity must read as an event", [_at(ref, location)])
    _validate_weekend_activities(context, state, audit)
    for ref, fact in facts.items():
        if not (
            fact.get("private_message_fact")
            and fact.get("type") == "bring"
            and selections[ref].get("decision") == "omitted"
        ):
            continue
        displayed_events = [
            _at(other_ref, selections[other_ref].get("location"))
            for other_ref, other in facts.items()
            if other.get("private_message_fact")
            and other.get("type") == "event"
            and other.get("child") == fact.get("child")
            and other.get("effective_date") == fact.get("effective_date")
            and selections[other_ref].get("decision") != "omitted"
            and selections[other_ref].get("location") != "weekend_overflow"
        ]
        if displayed_events:
            # The child's event for that date is on the board, so its bring-item must be too.
            raise RuleViolation("private event row must include its retained bring-item",
                                [ref, *displayed_events])
    # A weekend row showing an activity reads as that event, without `Besked:`. A fact
    # merged into it, such as the party's bring-item, belongs to the same row.
    activity_rows = {
        _weekend_row(selections[ref].get("location"))
        for ref, fact in facts.items()
        if (fact.get("confirmed_activity_kind") or fact.get("activity_kind"))
        and selections[ref]["decision"] != "omitted"
    } - {None}
    for selection in audit["facts"]:
        # An activity counted in the weekend overflow has no visible text to check.
        if selection.get("decision") == "omitted" or selection.get("location") == "weekend_overflow":
            continue
        fact = facts[selection["ref"]]
        location = str(selection.get("location", ""))
        visible = _value_at_location(state, location).casefold()
        weekend_activity = location.startswith("weekend") and bool(
            fact.get("confirmed_activity_kind") or fact.get("activity_kind")
            or _weekend_row(location) in activity_rows)
        involved = [_at(selection["ref"], location)]
        if fact.get("private_message_fact") and not weekend_activity and "besked" not in visible:
            raise RuleViolation("private message fact must show message provenance", involved)
        if fact.get("type") == "bring":
            if fact.get("actionability") == "required" and not re.search(
                r"\b(medbring|tag|skal have|husk)\b", visible
            ):
                raise RuleViolation("required bring-item must state the action", involved)
            if fact.get("actionability") == "recommended" and not re.search(
                r"\b(valgfri|valgfrit|kan|må|evt|eventuelt)\b", visible
            ):
                raise RuleViolation("recommended bring-item must remain visibly optional", involved)
        if fact.get("type") == "homework" and not re.search(
            r"\b(læs|lav|skriv|øv|aflever|regn|lektie)\b", visible
        ):
            raise RuleViolation("homework must retain an explicit action verb", involved)
    selections = {item["ref"]: item for item in audit["facts"]}
    for fact in context["items"]:
        selection = selections.get(fact["ref"], {})
        if fact.get("type") != "substitute" or selection.get("decision") == "omitted":
            continue
        match = re.fullmatch(r"(.+?) er vikar i (.+)", str(fact.get("title") or ""))
        if not match:
            continue
        teacher, scope = (part.strip() for part in match.groups())
        visible = _value_at_location(state, selection.get("location", "")).casefold()
        scope_parts = [part.strip() for part in re.split(r"\s*[·|/]\s*", scope) if part.strip()]
        if ("vikar:" not in visible
                or not any(form in visible for form in _person_display_forms(teacher))
                or any(part.casefold() not in visible for part in scope_parts)):
            raise RuleViolation("displayed substitute must retain name and lesson/class",
                                [_at(fact["ref"], selection.get("location"))])


def _strings(value):
    if isinstance(value, dict):
        for child in value.values():
            yield from _strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _strings(child)
    elif isinstance(value, str):
        yield value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("context", type=Path)
    parser.add_argument("state", type=Path)
    parser.add_argument("audit", type=Path)
    args = parser.parse_args()
    values = [json.loads(p.read_text()) for p in (args.context, args.state, args.audit)]
    validate_composition(*values)
    print("State, source freshness, inbox totals and complete selection audit verified")
