"""The parts of the board that follow fixed rules, written by code.

The composition model decides what matters and how to word it. Code owns what
must always be right: the weekend rows and their overflow count, and the review
count. What a line is (its label, box and colour) comes from its facts through
`skoleklar.present`, never from its words. Where code writes a line itself (the
weekend rows, the days ahead, the plain board), `fact_text` words it from the
fact's typed fields. `dashboard_build` writes the rest of the board from the
model's slots, or from code's own plain selection when the model fails.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from skoleklar import fact as fact_model
from skoleklar.dashboard_events import ACTIVITY_LABELS, activity_title, birthday_notice
from skoleklar.dashboard_timing import dashboard_focus, dashboard_weekend_dates
from skoleklar.present import line

WEEKEND_ROWS = 2
WEEKEND_TEXT_LIMIT = 48
LINE_LIMIT = 100
HERO_LINES = 5
WEEKDAY_LINES = 3
SHARED_LINES = 2
SHORT_DAYS = ("Man.", "Tir.", "Ons.", "Tor.", "Fre.", "Lør.", "Søn.")
DAY_NAMES = ("Mandag", "Tirsdag", "Onsdag", "Torsdag", "Fredag", "Lørdag", "Søndag")
MONTHS = ("jan.", "feb.", "mar.", "apr.", "maj", "jun.", "jul.", "aug.", "sep.", "okt.", "nov.", "dec.")


def _now(context):
    return datetime.fromisoformat(context["now"].replace("Z", "+00:00"))


def short_day(value: date) -> str:
    return f"{SHORT_DAYS[value.weekday()]} {value.day}. {MONTHS[value.month - 1]}"


def day_title(value: date) -> str:
    return f"{DAY_NAMES[value.weekday()]} {value.day}. {MONTHS[value.month - 1]}"


def _fit(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def short_name(name: str) -> str:
    """'Alex W.' for 'Alex Wilson': a first name and the last name's initials."""
    parts = str(name).split()
    if len(parts) < 2:
        return " ".join(parts)
    return parts[0] + " " + "-".join(part[0] + "." for part in parts[-1].split("-") if part)


def fact_text(fact, limit: int = LINE_LIMIT) -> str:
    """Code's wording of one fact, from its typed fields: no label, which `present` draws.

    Things to bring are their items; a substitute is the teacher and the subject;
    anything else is the fact's title.
    """
    title = " ".join(str(fact.get("title") or "").split())
    kind = fact_model.fact_type(fact)
    if kind == "bring" and fact.get("bring_items"):
        text = ", ".join(fact["bring_items"])
    elif kind == "substitute" and fact.get("teacher"):
        text = " · ".join(part for part in (short_name(fact["teacher"]), fact.get("school_subject")) if part)
    else:
        text = title
    return _fit(text or title or "Punkt", limit)


# --- Weekend rows -------------------------------------------------------------

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


def _weekend_entries(context):
    """The current weekend's activities and other facts, in display priority."""
    now = _now(context)
    dates = {value.isoformat() for value in dashboard_weekend_dates(now)}
    facts = [fact for fact in context.get("items", []) if fact.get("effective_date") in dates]
    cancelled_threads = {(f.get("child"), f.get("effective_date"), f.get("message_thread"))
                         for f in context.get("items", []) if f.get("cancelled") and f.get("message_thread")}
    decisions = {}
    activities = {}
    for fact in facts:
        if birthday_notice(fact):
            decisions[fact["ref"]] = ("omitted", "", "Classmate birth date only")
            continue
        if fact_model.kind(fact) == "routine":
            decisions[fact["ref"]] = ("omitted", "", "Weekly routine")
            continue
        kind = fact.get("confirmed_activity_kind") or fact.get("activity_kind")
        if kind:
            group = activities.setdefault(_activity_event(fact), {
                "child": fact["child"], "date": fact["effective_date"], "kind": kind,
                "title": activity_title(kind, fact.get("celebrant")),
                "thread": fact.get("message_thread"), "refs": [], "facts": [], "bring": [],
                "cancelled": False})
            group["refs"].append(fact["ref"])
            group["facts"].append(fact)
            if fact.get("cancelled") or (fact.get("child"), fact.get("effective_date"),
                                         fact.get("message_thread")) in cancelled_threads:
                group["cancelled"] = True
    others = []
    for fact in facts:
        if fact["ref"] in decisions or fact.get("confirmed_activity_kind") or fact.get("activity_kind"):
            continue
        if fact.get("type") == "bring":
            if fact.get("message_thread") and any(
                    g["cancelled"] and g["thread"] == fact.get("message_thread")
                    and g["child"] == fact.get("child") and g["date"] == fact["effective_date"]
                    for g in activities.values()):
                decisions[fact["ref"]] = ("omitted", "", "Cancelled activity")
                continue
            # A bring-item joins its activity: the same message thread first, then the same day.
            owner = next((g for g in activities.values() if not g["cancelled"]
                          and g["child"] == fact.get("child") and g["date"] == fact["effective_date"]
                          and g["thread"] and g["thread"] == fact.get("message_thread")), None) \
                or next((g for g in activities.values() if not g["cancelled"]
                         and g["child"] == fact.get("child") and g["date"] == fact["effective_date"]), None)
            if owner is not None:
                owner["bring"].append(fact)
                continue
        if fact.get("needs_review") and fact.get("type") != "bring":
            decisions[fact["ref"]] = ("omitted", "", "Needs review")
            continue
        others.append(fact)
    return activities, others, decisions


def _activity_lines(group, day):
    """The activity's line, then a line each for what to bring and what may come along."""
    title = group["title"]
    if len(f"{group['child']}: {title}") > WEEKEND_TEXT_LIMIT:
        # A long name gives way before the activity could be cut.
        title = ACTIVITY_LABELS[group["kind"]]
    facts = [{**fact, "cancelled": True} if group["cancelled"] else fact for fact in group["facts"]]
    lines = [line(title, group["refs"], facts, day=day)]
    for wanted in ("bring", "optional"):
        bring = [fact for fact in group["bring"] if fact_model.kind(fact) == wanted]
        if bring:
            text = ", ".join(dict.fromkeys(fact_text(fact) for fact in bring))
            lines.append(line(_fit(text, WEEKEND_TEXT_LIMIT), [fact["ref"] for fact in bring], bring,
                              day=day))
    return lines


def weekend_selection(context):
    """(rows, overflow, decisions): the weekend area and an audit decision per weekend fact.

    Rows hold activities first, choosing those with a bring-item when there are
    more activities than rows, then cancelled activities and other weekend facts.
    Every activity is shown or counted in the overflow; rows read chronologically.
    """
    activities, others, decisions = _weekend_entries(context)
    names = list(context.get("children", []))
    order = {name: index for index, name in enumerate(names)}
    active = [g for g in activities.values() if not g["cancelled"]]
    active.sort(key=lambda g: (not any(f.get("actionability") != "recommended" for f in g["bring"]),
                               g["date"], order.get(g["child"], 9), str(g["thread"])))
    shown = active[:WEEKEND_ROWS]
    hidden = active[WEEKEND_ROWS:]
    cancelled = [g for g in activities.values() if g["cancelled"]]
    entries = [("activity", g) for g in shown]
    for group in cancelled:
        if len(entries) < WEEKEND_ROWS:
            entries.append(("activity", group))
        else:
            for ref in group["refs"]:
                decisions[ref] = ("omitted", "", "Cancelled activity")
            for fact in group["bring"]:
                decisions[fact["ref"]] = ("omitted", "", "Cancelled activity")
    priority = {"bring": 0, "parent_action": 1, "homework": 1, "schedule_change": 2, "event": 3}
    others.sort(key=lambda f: (priority.get(f.get("type"), 4), f["effective_date"], f["ref"]))
    for fact in others:
        if len(entries) < WEEKEND_ROWS:
            entries.append(("fact", fact))
        else:
            decisions[fact["ref"]] = ("omitted", "", "Weekend rows full")
    entries.sort(key=lambda entry: (entry[1]["date"] if entry[0] == "activity" else entry[1]["effective_date"],
                                    order.get(entry[1]["child"], 9)))
    rows = []
    for index, (kind, value) in enumerate(entries):
        location = f"weekend[{index}]"
        if kind == "activity":
            day = date.fromisoformat(value["date"])
            who = value["child"] if value["child"] in order else "Begge"
            rows.append({"date": day.isoformat(), "day": short_day(day), "who": who,
                         "lines": _activity_lines(value, day)})
            first, *rest = value["refs"]
            decisions[first] = ("displayed", location, "Weekend activity")
            for ref in rest:
                decisions[ref] = ("merged", location, "Same weekend activity")
            for fact in value["bring"]:
                decisions[fact["ref"]] = ("merged", location, "Bring item for activity")
        else:
            day = date.fromisoformat(value["effective_date"])
            who = "Begge" if value.get("child") not in order else value["child"]
            rows.append({"date": day.isoformat(), "day": short_day(day), "who": who,
                         "lines": [line(fact_text(value, WEEKEND_TEXT_LIMIT), [value["ref"]], [value],
                                        day=day)]})
            decisions[value["ref"]] = ("displayed", location, "Weekend fact")
    for group in hidden:
        for ref in group["refs"]:
            decisions[ref] = ("merged", "weekend_overflow", "Weekend activity overflow")
        for fact in group["bring"]:
            decisions[fact["ref"]] = ("merged", "weekend_overflow", "Weekend activity overflow")
    return rows, len(hidden), decisions


def apply_weekend(state, audit, context):
    """Replace the weekend area and its audit entries with the code-built selection."""
    rows, overflow, decisions = weekend_selection(context)
    state["weekend"] = rows
    state["weekend_overflow"] = overflow
    for selection in audit.get("facts", []):
        decision = decisions.get(selection.get("ref"))
        if decision is not None:
            selection["decision"], selection["location"], selection["reason"] = decision
        elif str(selection.get("location") or "").startswith("weekend") and selection.get("decision") != "omitted":
            # Only weekend facts belong in the weekend area, and code fills it.
            selection.update(decision="omitted", location="", reason="Not a weekend fact")


def review_count(audit, context):
    """Facts flagged for review in the shown week and weekend that the board leaves out."""
    now = _now(context)
    monday = dashboard_focus(now).school_date - timedelta(days=dashboard_focus(now).school_date.weekday())
    window = {(monday + timedelta(days=offset)).isoformat() for offset in range(7)}
    window |= {value.isoformat() for value in dashboard_weekend_dates(now)}
    omitted = {item["ref"] for item in audit.get("facts", []) if item.get("decision") == "omitted"}
    return min(99, sum(1 for fact in context.get("items", [])
                       if fact.get("needs_review") and fact["ref"] in omitted
                       and fact.get("effective_date") in window and not birthday_notice(fact)))


def finalize(state, audit, context):
    """Apply the code-owned parts to a composed state and audit, in place."""
    if not context.get("now"):
        return
    apply_weekend(state, audit, context)
    _set_review_count(state, audit, context)


def _set_review_count(state, audit, context):
    count = review_count(audit, context)
    if count:
        state["review_count"] = count
    else:
        state.pop("review_count", None)


# --- Plain board ----------------------------------------------------------------

_PRIORITY = {"bring": 0, "homework": 0, "parent_action": 1, "schedule_change": 1,
             "substitute": 2, "event": 3}


def _fact_priority(fact):
    required = fact.get("actionability") == "required"
    return (_PRIORITY.get(fact.get("type"), 4) - (1 if required else 0),
            bool(fact.get("needs_review")), fact["ref"])
