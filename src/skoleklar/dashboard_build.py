"""Build the board from slots: the model's selection, or code's plain one.

The composition model (`dashboard-v3`) returns only judgement: per child and
day, lines of fact references with a priority and a short text, plus what it
left out. This module writes the board state (schema 3), where every line is an
object with its wording, the facts it shows and how it looks (`skoleklar.present`).
It is elastic where an older contract rejected the whole board:

- dates, day labels, the week label and the update stamp come from the calendar;
- a line keeps the model's wording; code adds nothing to it, and its label, box
  and colour come from its facts;
- weekend rows, the overflow count, the inbox and freshness come from the facts;
- a reference that is unknown, repeated or on a date it does not cover is
  dropped from that line, and a line left without facts disappears;
- a required fact for today or later that the model neither placed nor omitted
  is added to its day, worded from its typed fields; too many lines become a
  `+N punkter mere` line.

The result is checked by `validate_composition`; a failure there is a bug or a
rule this module cannot repair, and the caller retries or falls back.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date

from skoleklar import fact as fact_model
from skoleklar import health
from skoleklar.dashboard_events import birthday_notice
from skoleklar.dashboard_rules import (
    HERO_LINES,
    SHARED_LINES,
    LINE_LIMIT,
    SHORT_DAYS,
    MONTHS,
    WEEKDAY_LINES,
    _fact_priority,
    _fit,
    _now,
    _set_review_count,
    fact_text,
    weekend_selection,
)
from skoleklar.dashboard_timing import dashboard_focus
from skoleklar.present import line, more_line

SCHEMA_VERSION = 3
PRIORITIES = ("obligation", "change", "event", "context")
_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️‍]")
_REQUIRED_TYPES = {"bring", "homework", "parent_action"}
_CHANGE_TYPES = {"schedule_change", "substitute"}


def _clean(value, limit):
    text = " ".join(_EMOJI.sub("", str(value or "")).split())
    return _fit(text, limit) if text else ""


def _covers(fact, day):
    if not fact.get("effective_date"):
        return False
    start = date.fromisoformat(fact["effective_date"])
    end = date.fromisoformat(fact["end_date"]) if fact.get("end_date") else start
    return start <= day <= max(start, end)


def _targets(fact, names, weekdays, hero_date):
    """Where code puts a fact itself: its first shown day (and the hero), or that day's shared line."""
    covered = [d for d, day in enumerate(weekdays) if _covers(fact, day)]
    if not covered:
        return []
    d = covered[0]
    if fact.get("child") not in names:
        return [(d, "shared")]
    child = names.index(fact["child"])
    return [(d, child)] + ([("hero", child)] if weekdays[d] == hero_date else [])


def _omission_reason(fact, omission, targets, sections, shown, facts=None):
    """The audit reason when leaving `fact` out holds against the facts, else None.

    Facts code always leaves out need no tag: classmate birth dates, facts outside
    the shown week and facts flagged for review. Otherwise the model's tag must hold.
    A duplicate must be of a shown fact for the same child: the parents' board gives
    each child a row, so one child's conference cannot hide behind the other's line.
    """
    if birthday_notice(fact):
        return "Classmate birth date only"
    if fact_model.kind(fact) == "routine":
        return "Weekly routine"
    if not targets:
        return "Outside displayed week"
    if fact.get("needs_review"):
        return "Needs review"
    tag, same_as = omission or (None, None)
    informational = (fact.get("actionability") == "informational" and not fact.get("cancelled")
                     and fact.get("type") not in _CHANGE_TYPES | {"event"})
    if tag == "duplicate" and same_as in shown and (
            facts is None or facts[same_as].get("child") == fact.get("child")):
        return f"Duplicate of {same_as}"
    if tag == "capacity" and all(len(sections[key]["entries"]) >= sections[key].get("limit", 1)
                                 for key in targets):
        return "Board capacity"
    if tag == "other_class":
        return "Other class"
    if tag == "not_relevant" and informational:
        return "Not relevant"
    return None


def build_state(context, slots, *, plain=False):
    """The board state and audit for `context` from `slots`; see the module docstring."""
    from skoleklar.dashboard_composition import _apply_inbox, school_calendar
    from skoleklar.dashboard_validation import validate_composition

    now = _now(context)
    focus = dashboard_focus(now)
    calendar = school_calendar(context)
    names = list(context["children"])
    facts = {fact["ref"]: fact for fact in context["items"]}
    hero_date = date.fromisoformat(calendar["hero"]["date"])
    weekdays = [date.fromisoformat(day["date"]) for day in calendar["weekdays"]]
    rows, overflow, decisions = weekend_selection(context)
    weekend_refs = set(decisions)
    slots = slots if isinstance(slots, dict) else {}
    omissions = {}
    for item in slots.get("omitted") or []:
        if isinstance(item, dict) and item.get("ref") in facts:
            omissions.setdefault(item["ref"], (item.get("reason"), item.get("same_as")))
    dropped = {}

    def entries_for(lines, day):
        """[(text, refs, rank)] for a section's model lines, keeping only valid references."""
        result, used = [], set()
        for entry in lines if isinstance(lines, list) else []:
            if not isinstance(entry, dict):
                continue
            refs = []
            for ref in entry.get("refs") or []:
                fact = facts.get(ref)
                if fact is None or ref in used or ref in weekend_refs or birthday_notice(fact):
                    continue
                if fact_model.kind(fact) == "routine":
                    # A weekly routine is never drawn, so it never takes one of a day's lines.
                    dropped.setdefault(ref, "Weekly routine")
                    continue
                if not _covers(fact, day):
                    dropped.setdefault(ref, "Wrong date")
                    continue
                refs.append(ref)
            if not refs:
                continue
            used.update(refs)
            rank = PRIORITIES.index(entry["priority"]) if entry.get("priority") in PRIORITIES else 3
            result.append([_clean(entry.get("text"), LINE_LIMIT), refs, rank])
        return result

    def child_slots(section):
        found = {}
        for child in (section or {}).get("children") or []:
            if isinstance(child, dict) and child.get("name") in names and child["name"] not in found:
                found[child["name"]] = child
        return [found.get(name, {"name": name, "lines": []}) for name in names]

    model_days = {}
    for day in slots.get("weekdays") or []:
        if isinstance(day, dict) and isinstance(day.get("date"), str):
            model_days.setdefault(day["date"], day)

    # Sections: hero per child, then each weekday per child and its shared line.
    sections = {}
    for c, child in enumerate(child_slots(slots.get("hero"))):
        sections[("hero", c)] = {"day": hero_date, "name": names[c],
                                 "entries": entries_for(child.get("lines"), hero_date), "limit": HERO_LINES,
                                 "location": lambda i, c=c: f"hero.children[{c}].lines[{i}]"}
    for d, day in enumerate(weekdays):
        model = model_days.get(day.isoformat(), {})
        for c, child in enumerate(child_slots(model)):
            sections[(d, c)] = {"day": day, "name": names[c],
                                "entries": entries_for(child.get("lines"), day), "limit": WEEKDAY_LINES,
                                "location": lambda i, d=d, c=c: f"weekdays[{d}].children[{c}].lines[{i}]"}
        shared = model.get("shared")
        sections[(d, "shared")] = {"day": day, "entries": entries_for([shared] if shared else [], day)[:1],
                                   "limit": SHARED_LINES,
                                   "location": lambda i, d=d: f"weekdays[{d}].shared[{i}]"}

    # Safe default: a fact inside the shown week stays on the board unless its
    # omission tag holds against the facts (see `_omission_reason`).
    reasons = {}
    for ref, fact in sorted(facts.items(), key=lambda item: _fact_priority(item[1])):
        placed = {r for section in sections.values() for entry in section["entries"] for r in entry[1]}
        if ref in placed or ref in weekend_refs:
            continue
        targets = _targets(fact, names, weekdays, hero_date)
        reason = _omission_reason(fact, omissions.get(ref), targets, sections, placed, facts)
        if reason:
            reasons[ref] = reason
            continue
        rank = 0 if fact.get("type") in _REQUIRED_TYPES else 1 if fact.get("type") in _CHANGE_TYPES else 2
        for key in targets:
            sections[key]["entries"].append(["", [ref], rank])
        dropped.pop(ref, None)

    placements = {}
    capacity = {}

    def place(ref, decision, location, reason):
        if ref not in placements:
            placements[ref] = (decision, location, reason)

    def board_line(text, refs, day):
        members = [facts[ref] for ref in refs]
        return line(text or fact_text(members[0]), refs, members, day=day)

    def render(section):
        entries = section["entries"]
        limit = section["limit"]
        shown = entries if len(entries) <= limit else entries[: limit - 1]
        lines = []
        for index, (text, refs, _) in enumerate(shown):
            lines.append(board_line(text, refs, section["day"]))
            location = section["location"](index)
            place(refs[0], "displayed", location, "Selected")
            for ref in refs[1:]:
                place(ref, "merged", location, "Same thing")
        hidden = [ref for _, refs, _ in entries[len(shown):] for ref in refs]
        if hidden:
            lines.append(more_line(len(hidden)))
            for ref in hidden:
                capacity[ref] = "Board capacity"
        return lines

    hero_children = [{"name": names[c], "lines": render(sections[("hero", c)])} for c in range(len(names))]
    days = []
    for d, day in enumerate(weekdays):
        children = [{"name": names[c], "lines": render(sections[(d, c)])} for c in range(len(names))]
        days.append({"date": day.isoformat(), "day": calendar["weekdays"][d]["day"],
                     "children": children, "shared": render(sections[(d, "shared")])})

    audit_facts = []
    for fact in context["items"]:
        ref = fact["ref"]
        if ref in decisions:
            decision, location, reason = decisions[ref]
        elif ref in placements:
            decision, location, reason = placements[ref]
        else:
            decision, location = "omitted", ""
            reason = capacity.get(ref) or reasons.get(ref) or dropped.get(ref) or "Not selected"
        audit_facts.append({"ref": ref, "decision": decision, "location": location, "reason": reason})
    limitations = [_clean(item, 160) for item in slots.get("limitations") or [] if _clean(item, 160)][:6]

    updated = _local(context["freshness"]["source_updated_at"])
    state = {
        "schema_version": SCHEMA_VERSION,
        "week_label": calendar["week_label"],
        "updated_label": (f"DATA {SHORT_DAYS[updated.weekday()].rstrip('.').upper()} {updated.day}. "
                          f"{MONTHS[updated.month - 1].upper()} · {updated:%H.%M}"),
        "freshness": dict(context["freshness"]),
        "hero": {"state": "I dag" if hero_date == now.date() and focus.mode == "schoolday" else "Næste skoledag",
                 "day": calendar["hero"]["day"], "date": hero_date.isoformat(), "children": hero_children},
        "weekdays": days,
        "weekend": rows,
        "weekend_overflow": overflow,
        "messages": {key: context["messages"][key] for key in ("checked", "bodies_retained", "unread_count")}
        | {"items": []},
        # The plain board says so in the board's one line about what is missing. The
        # scan sets this again for every frame, so a cached week never carries it.
        "health": {"composition": health.degraded("plain_board") if plain else health.ok()},
    }
    _apply_inbox(state["messages"], context)
    audit = {"source_updated_at": context["freshness"]["source_updated_at"], "facts": audit_facts,
             "limitations": limitations}
    _set_review_count(state, audit, context)
    validate_composition(context, state, audit)
    return state, audit


def _local(value):
    from datetime import datetime
    from skoleklar.dashboard_timing import ZONE
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(ZONE)


def fallback_slots(context):
    """Code's own selection: every certain fact on its day, obligations first."""
    from skoleklar.dashboard_composition import school_calendar

    calendar = school_calendar(context)
    names = list(context["children"])
    weekdays = [date.fromisoformat(day["date"]) for day in calendar["weekdays"]]
    hero_date = date.fromisoformat(calendar["hero"]["date"])
    by_slot = defaultdict(list)
    omitted = []
    for fact in context["items"]:
        day = date.fromisoformat(fact["effective_date"]) if fact.get("effective_date") else None
        if birthday_notice(fact):
            omitted.append({"ref": fact["ref"], "reason": "birthday_notice", "same_as": None})
        elif fact.get("needs_review"):
            omitted.append({"ref": fact["ref"], "reason": "uncertain", "same_as": None})
        elif day not in weekdays:
            omitted.append({"ref": fact["ref"], "reason": "outside_week", "same_as": None})
        else:
            by_slot[(day, fact.get("child") if fact.get("child") in names else None)].append(fact)

    def lines(day, name):
        grouped = {}
        for fact in sorted(by_slot.get((day, name), []), key=_fact_priority):
            text = fact_text(fact)
            if text not in grouped:
                rank = 0 if fact.get("type") in _REQUIRED_TYPES else 1 if fact.get("type") in _CHANGE_TYPES else 2
                grouped[text] = {"refs": [], "priority": PRIORITIES[rank], "text": text}
            grouped[text]["refs"].append(fact["ref"])
        return list(grouped.values())

    def shared(day):
        found = lines(day, None)
        if not found:
            return None
        return {"refs": [ref for line in found for ref in line["refs"]], "priority": found[0]["priority"],
                "text": _fit("; ".join(line["text"] for line in found), 90)}

    return {
        "hero": {"children": [{"name": name, "lines": lines(hero_date, name)} for name in names]},
        "weekdays": [{"date": day.isoformat(),
                      "children": [{"name": name, "lines": lines(day, name)} for name in names],
                      "shared": shared(day)} for day in weekdays],
        "omitted": omitted,
        "limitations": ["Plain fallback board: the composition model did not produce a valid board."],
    }


def fallback_composition(context):
    """A plain, valid board from the facts alone; its health says `enkel visning`."""
    return build_state(context, fallback_slots(context), plain=True)


def main():
    """Build the board state and selection audit from a context and slots, as the cloud does."""
    import argparse
    import json
    import os
    from pathlib import Path

    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument("context", type=Path, help="Context from prepare_context.py")
    parser.add_argument("slots", type=Path, help="Slots following manifests/dashboard-v3")
    parser.add_argument("--state", type=Path, required=True, help="Where to write the board state")
    parser.add_argument("--selection", type=Path, required=True, help="Where to write the selection audit")
    args = parser.parse_args()
    context = json.loads(args.context.read_text())
    state, audit = build_state(context, json.loads(args.slots.read_text()))
    for path, value in ((args.state, state), (args.selection, audit)):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    shown = sum(item["decision"] != "omitted" for item in audit["facts"])
    print(json.dumps({"state": str(args.state), "selection": str(args.selection),
                      "facts_shown": shown, "facts_total": len(audit["facts"]),
                      "review_count": state.get("review_count", 0)}))


if __name__ == "__main__":
    main()
