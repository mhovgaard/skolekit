"""The parts of the board that follow fixed rules, written by code.

The composition model decides what matters and how to word it. Code owns what
must always be right: the weekend rows and their overflow count, the markers a
line needs because of the fact behind it (`Besked:`, `Medbring:`, `Valgfrit:`,
`Vikar:`), and the review count. `dashboard_build` writes the rest of the board
from the model's slots, or from code's own plain selection when the model fails.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime, timedelta

from skoleklar.dashboard_events import ACTIVITY_LABELS, birthday_notice
from skoleklar.dashboard_timing import dashboard_focus, dashboard_weekend_dates
from skoleklar.dashboard_validation import _activity_event, _person_display_forms

WEEKEND_ROWS = 2
WEEKEND_TEXT_LIMIT = 48
LINE_LIMIT = 100
HERO_LINES = 5
WEEKDAY_LINES = 3
SHORT_DAYS = ("Man.", "Tir.", "Ons.", "Tor.", "Fre.", "Lør.", "Søn.")
DAY_NAMES = ("Mandag", "Tirsdag", "Onsdag", "Torsdag", "Fredag", "Lørdag", "Søndag")
MONTHS = ("jan.", "feb.", "mar.", "apr.", "maj", "jun.", "jul.", "aug.", "sep.", "okt.", "nov.", "dec.")
_REQUIRED_BRING = re.compile(r"\b(medbring|tag|skal have|husk)\b")
_OPTIONAL = re.compile(r"\b(valgfri|valgfrit|kan|må|evt|eventuelt)\b")
_HOMEWORK = re.compile(r"\b(læs|lav|skriv|øv|aflever|regn|lektie)\b")
_LINE_LOCATION = re.compile(
    r"(?:hero\.children\[\d\]\.lines\[\d+\]|weekdays\[\d\]\.children\[\d\]\.lines\[\d+\]|weekdays\[\d\]\.shared)"
)


def _now(context):
    return datetime.fromisoformat(context["now"].replace("Z", "+00:00"))


def short_day(value: date) -> str:
    return f"{SHORT_DAYS[value.weekday()]} {value.day}. {MONTHS[value.month - 1]}"


def day_title(value: date) -> str:
    return f"{DAY_NAMES[value.weekday()]} {value.day}. {MONTHS[value.month - 1]}"


def _fit(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def bring_items(fact) -> str:
    """The objects of a bring fact, without a leading `Medbring`, `Husk` or `Tag`."""
    title = str(fact.get("title") or "").strip()
    return re.sub(r"^(medbring|husk|tag)\s*:?\s*", "", title, flags=re.IGNORECASE)


def fact_text(fact) -> str:
    """A line for one fact, with every marker the fact's type and source require."""
    title = str(fact.get("title") or "").strip()
    kind = fact.get("type")
    if kind == "bring":
        items = bring_items(fact)
        text = f"Valgfrit: {items}" if fact.get("actionability") == "recommended" else f"Medbring: {items}"
    elif kind == "substitute":
        text = substitute_text(fact) or title
    elif kind == "homework" and not _HOMEWORK.search(title.casefold()):
        text = f"Lektie: {title}"
    elif kind == "parent_action" and fact.get("actionability") == "recommended" \
            and not _OPTIONAL.search(title.casefold()):
        text = f"Valgfrit: {title}"
    else:
        text = title
    if fact.get("cancelled") and "aflyst" not in text.casefold():
        text += " aflyst"
    if fact.get("private_message_fact") and "besked" not in text.casefold():
        text = f"Besked: {text}"
    return _fit(text, LINE_LIMIT)


def substitute_text(fact) -> str | None:
    """`Vikar: Alex W. · KULTUR 00` from a `NAME er vikar i SUBJECT · CLASS` fact."""
    match = re.fullmatch(r"(.+?) er vikar i (.+)", str(fact.get("title") or ""))
    if not match:
        return None
    teacher, scope = (part.strip() for part in match.groups())
    parts = teacher.split()
    name = parts[0] + (" " + "-".join(p[0] + "." for p in parts[-1].split("-") if p)
                       if len(parts) > 1 else "")
    scope = " ".join(part.strip() for part in re.split(r"\s*[·|/]\s*", scope) if part.strip())
    return _fit(f"Vikar: {name} · {scope}", LINE_LIMIT)


# --- Weekend rows -------------------------------------------------------------

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
        kind = fact.get("confirmed_activity_kind") or fact.get("activity_kind")
        if kind:
            group = activities.setdefault(_activity_event(fact), {
                "child": fact["child"], "date": fact["effective_date"], "kind": kind,
                "thread": fact.get("message_thread"), "refs": [], "bring": [], "cancelled": False})
            group["refs"].append(fact["ref"])
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


def _activity_text(group, names_order):
    label = ACTIVITY_LABELS[group["kind"]] + (" aflyst" if group["cancelled"] else "")
    text = f"{group['child']}: {label}"
    required = [bring_items(f) for f in group["bring"] if f.get("actionability") != "recommended"]
    optional = [bring_items(f) for f in group["bring"] if f.get("actionability") == "recommended"]
    for marker, items in (("Medbring", required), ("Valgfrit", optional)):
        if not items:
            continue
        candidate = f"{text}; {marker}: {', '.join(items)}"
        if len(candidate) > WEEKEND_TEXT_LIMIT:
            candidate = _fit(f"{text}; {marker}: {items[0]} m.fl.", WEEKEND_TEXT_LIMIT)
        text = candidate
    return _fit(text, WEEKEND_TEXT_LIMIT)


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
            rows.append({"day": short_day(day), "text": _activity_text(value, names)})
            first, *rest = value["refs"]
            decisions[first] = ("displayed", location, "Weekend activity")
            for ref in rest:
                decisions[ref] = ("merged", location, "Same weekend activity")
            for fact in value["bring"]:
                decisions[fact["ref"]] = ("merged", location, "Bring item for activity")
        else:
            day = date.fromisoformat(value["effective_date"])
            who = "Begge" if value.get("child") not in order else value["child"]
            rows.append({"day": short_day(day), "text": _fit(f"{who}: {fact_text(value)}", WEEKEND_TEXT_LIMIT)})
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


# --- Marker repair --------------------------------------------------------------

def _line(state, location):
    match = re.fullmatch(r"hero\.children\[(\d)\]\.lines\[(\d+)\]", location)
    if match:
        lines = state["hero"]["children"][int(match[1])]["lines"]
        return lines, int(match[2])
    match = re.fullmatch(r"weekdays\[(\d)\]\.children\[(\d)\]\.lines\[(\d+)\]", location)
    if match:
        lines = state["weekdays"][int(match[1])]["children"][int(match[2])]["lines"]
        return lines, int(match[3])
    return None, None


def repair_markers(state, audit, context):
    """Add the markers a line's facts require when the model left them out.

    Only exact line locations are repaired. A recommended item never gains
    `Medbring:`, a required one never `Valgfrit:`. Returns the repaired locations.
    """
    facts = {fact["ref"]: fact for fact in context.get("items", [])}
    by_location = defaultdict(list)
    for selection in audit.get("facts", []):
        location = str(selection.get("location") or "")
        if selection.get("decision") != "omitted" and _LINE_LOCATION.fullmatch(location):
            fact = facts.get(selection.get("ref"))
            if fact is not None:
                by_location[location].append(fact)
    repaired = []
    for location, members in by_location.items():
        shared = re.fullmatch(r"weekdays\[(\d)\]\.shared", location)
        try:
            if shared:
                day = state["weekdays"][int(shared[1])]
                text = day["shared"]
            else:
                lines, index = _line(state, location)
                text = lines[index]
        except (IndexError, KeyError, TypeError):
            continue
        if not isinstance(text, str):
            continue
        fixed = _repair_line(text, members)
        if fixed != text:
            if shared:
                day["shared"] = fixed
            else:
                lines[index] = fixed
            repaired.append(location)
    return repaired


def _substitute_ok(text, fact):
    match = re.fullmatch(r"(.+?) er vikar i (.+)", str(fact.get("title") or ""))
    if not match:
        return True
    folded = text.casefold()
    scope = [part.strip() for part in re.split(r"\s*[·|/]\s*", match[2]) if part.strip()]
    return ("vikar:" in folded and any(form in folded for form in _person_display_forms(match[1].strip()))
            and all(part.casefold() in folded for part in scope))


def _repair_line(text, members):
    for fact in members:
        if fact.get("type") == "substitute" and not _substitute_ok(text, fact):
            text = substitute_text(fact) or text
    prefixes = []
    folded = text.casefold()
    for fact in members:
        kind, actionability = fact.get("type"), fact.get("actionability")
        if kind == "bring" and actionability == "required" and not _REQUIRED_BRING.search(folded) \
                and "Medbring:" not in prefixes:
            prefixes.append("Medbring:")
        if kind == "bring" and actionability == "recommended" and not _OPTIONAL.search(folded):
            if folded.startswith("medbring:"):
                text = "Valgfrit:" + text[len("medbring:"):]
                folded = text.casefold()
            elif "Valgfrit:" not in prefixes:
                prefixes.append("Valgfrit:")
        if kind == "homework" and not _HOMEWORK.search(folded) and "Lektie:" not in prefixes:
            prefixes.append("Lektie:")
        if kind == "parent_action" and actionability == "recommended" and not _OPTIONAL.search(folded) \
                and "Valgfrit:" not in prefixes:
            prefixes.append("Valgfrit:")
        if kind == "substitute" and "vikar" not in folded and "Vikar:" not in prefixes:
            prefixes.append("Vikar:")
    if any(fact.get("private_message_fact") for fact in members) and "besked" not in folded:
        prefixes.insert(0, "Besked:")
    return _fit(" ".join([*prefixes, text]), LINE_LIMIT) if prefixes else text


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
        return []
    apply_weekend(state, audit, context)
    repaired = repair_markers(state, audit, context)
    _set_review_count(state, audit, context)
    return repaired


def _set_review_count(state, audit, context):
    count = review_count(audit, context)
    if count:
        state["review_count"] = count
    else:
        state.pop("review_count", None)


# --- Fallback board -------------------------------------------------------------

_HEADINGS = {"bring": "Medbring", "homework": "Lektier", "parent_action": "Frist",
             "schedule_change": "Ændring", "substitute": "Vikar", "event": "Arrangement"}
_PRIORITY = {"bring": 0, "homework": 0, "parent_action": 1, "schedule_change": 1,
             "substitute": 2, "event": 3}


def _fact_priority(fact):
    required = fact.get("actionability") == "required"
    return (_PRIORITY.get(fact.get("type"), 4) - (1 if required else 0),
            bool(fact.get("needs_review")), fact["ref"])
