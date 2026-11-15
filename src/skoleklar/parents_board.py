"""Data for the parents' board, written by code: no model calls.

The parents' board gives each child a row across the panel. The left (dark) side
is the next school day; the right side looks four days ahead, then lists events
up to two weeks out. This module supplies what the composed week does not carry:

- `kinds`: what each board line is (homework, a trip, a deadline …), from the
  facts behind it, so the renderer can draw a checkbox, a category box or the
  yellow time-urgency marker without parsing Danish text;
- `ahead`: the look-ahead days the composed school week does not cover (the
  weekend and the next school week), written from the facts like the fallback board;
- `later_events`: trips, parties, meetings and required deadlines after the
  look-ahead, up to two weeks out.

See docs/parents-board.md.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime, timedelta

from skoleklar.dashboard_events import birthday_notice
from skoleklar.dashboard_rules import _fact_priority, _fit, fact_text
from skoleklar.dashboard_timing import ZONE, dashboard_focus

KINDS = ("homework", "bring", "optional", "deadline", "change", "substitute",
         "trip", "party", "meeting", "event", "info", "more")
EVENT_KINDS = ("trip", "party", "meeting", "event")
# Days after the next school day that the right side shows in full.
LOOKAHEAD_DAYS = 4
# Events and deadlines after the look-ahead are listed until this many days after
# the next school day; a frame's context must reach `CONTEXT_DAYS` ahead.
LATER_DAYS = 14
CONTEXT_DAYS = LATER_DAYS + 4
LATER_LIMIT = 6
AHEAD_LINES = 5
AHEAD_SHARED = 2
TEXT_LIMIT = 100
LATER_TEXT_LIMIT = 60
# Lower rank wins when one line joins facts of several kinds.
_RANK = {"homework": 0, "trip": 1, "party": 1, "meeting": 1, "event": 1, "deadline": 2,
         "change": 3, "bring": 4, "optional": 5, "substitute": 6, "info": 7, "more": 8}
_ACTIVITY_KINDS = {"class_trip": "trip", "birthday_party": "party",
                   "class_event": "event", "social_activity": "event"}
# Words that are not trips although they end in "tur".
_NOT_TRIPS = {"natur", "kultur", "litteratur", "temperatur", "struktur", "arkitektur",
              "figur", "frisur", "eventyr"}
_TRIP_WORDS = re.compile(r"\b(udflugt\w*|ekskursion\w*|lejrskole\w*|museum\w*|museet|teater\w*|"
                         r"teatret|biograf\w*|kulturdag\w*|koloni\w*)\b", re.IGNORECASE)
_WORD = re.compile(r"[\wæøåÆØÅ-]+")
_PARTY_WORDS = re.compile(r"fest|fødselsdag|jul\w*|halloween|fastelavn|disko|banko|lucia",
                          re.IGNORECASE)
# "Forældremøde" and "samtale" are meetings; a trip's "mødetid" is not.
_MEETING_WORDS = re.compile(r"møde(?:r|t)?\b|samtale|skole-hjem|konsultation", re.IGNORECASE)
_SIGN_UP = re.compile(r"tilmeld|frist|svar|aflever|underskriv|betal|book", re.IGNORECASE)
_MORE = re.compile(r"^\+\d+ punkt")
# A line that starts with a task is homework when its facts are unknown.
_HOMEWORK_START = re.compile(r"^(læs|lav|skriv|øv|regn|lær|færdiggør|aflever)\b", re.IGNORECASE)


def _is_trip(text: str) -> bool:
    if _TRIP_WORDS.search(text):
        return True
    for word in _WORD.findall(text):
        folded = word.casefold()
        # 'skovturen' and 'ture' are trips; 'kulturen' and 'naturfag' are not.
        stem = re.sub(r"(?:en|e)$", "", folded) if folded.endswith(("turen", "ture")) else folded
        if stem.endswith("tur") and stem not in _NOT_TRIPS:
            return True
    return False


def event_kind(text: str) -> str:
    """'trip', 'party', 'meeting' or 'event' for an event's title."""
    if _MEETING_WORDS.search(text):
        return "meeting"
    if _is_trip(text):
        return "trip"
    if _PARTY_WORDS.search(text):
        return "party"
    return "event"


def fact_kind(fact: dict) -> str:
    """What one fact is on the board."""
    kind, actionability = fact.get("type"), fact.get("actionability")
    title = str(fact.get("title") or "")
    if kind == "homework":
        return "homework"
    if kind == "bring":
        return "optional" if actionability == "recommended" else "bring"
    if kind == "parent_action":
        if _MEETING_WORDS.search(title) and not _SIGN_UP.search(title):
            return "meeting"
        return "optional" if actionability == "recommended" else "deadline"
    if kind == "schedule_change":
        return "change"
    if kind == "substitute":
        return "substitute"
    if kind == "event":
        activity = fact.get("confirmed_activity_kind") or fact.get("activity_kind")
        if activity in _ACTIVITY_KINDS and not _MEETING_WORDS.search(title):
            return _ACTIVITY_KINDS[activity]
        return event_kind(title)
    return "info"


def line_kind(members: list[dict]) -> str:
    """The kind of a line that joins `members`: homework first, then events, then the rest."""
    kinds = [fact_kind(member) for member in members] or ["info"]
    return min(kinds, key=lambda kind: _RANK[kind])


def text_kind(text: str) -> str:
    """A line's kind from its markers, for lines whose facts are unknown."""
    folded = str(text).strip().casefold()
    if _MORE.match(folded):
        return "more"
    body = re.sub(r"^besked:\s*", "", folded)
    for marker, kind in (("lektie:", "homework"), ("medbring:", "bring"), ("valgfrit:", "optional"),
                         ("vikar:", "substitute"), ("frist", "deadline"), ("tilmeld", "deadline")):
        if body.startswith(marker):
            return kind
    if folded.startswith("besked:"):
        return event_kind(body)
    if _HOMEWORK_START.match(body):
        return "homework"
    return "info"


def attach_kinds(state: dict, audit: dict, context: dict) -> dict:
    """Give every hero and weekday child a `kinds` list aligned with its lines, in place.

    The audit names the board location of each displayed or merged fact, so the
    kinds come from the facts themselves. A cached composition from before kinds
    existed gets them here too. Returns `state`.
    """
    facts = {fact["ref"]: fact for fact in context.get("items", [])}
    by_location = defaultdict(list)
    for item in audit.get("facts", []):
        if item.get("decision") in {"displayed", "merged"} and item.get("ref") in facts:
            by_location[str(item.get("location") or "")].append(facts[item["ref"]])

    def kinds(prefix, lines):
        result = []
        for index, text in enumerate(lines):
            members = by_location.get(f"{prefix}.lines[{index}]")
            result.append(line_kind(members) if members else text_kind(text))
        return result

    for c, child in enumerate(state["hero"]["children"]):
        child["kinds"] = kinds(f"hero.children[{c}]", child["lines"])
    for d, day in enumerate(state.get("weekdays", [])):
        for c, child in enumerate(day["children"]):
            child["kinds"] = kinds(f"weekdays[{d}].children[{c}]", child["lines"])
        if day.get("shared"):
            members = by_location.get(f"weekdays[{d}].shared")
            day["shared_kind"] = line_kind(members) if members else text_kind(day["shared"])
        else:
            day.pop("shared_kind", None)
    return state


def _now(context: dict) -> datetime:
    return datetime.fromisoformat(context["now"].replace("Z", "+00:00")).astimezone(ZONE)


def _usable(fact: dict) -> bool:
    return bool(fact.get("effective_date")) and not fact.get("needs_review") and not birthday_notice(fact)


def _lines(facts: list[dict]) -> list[tuple[str, str]]:
    """(text, kind) per distinct fact text, most important first, as the fallback board writes them."""
    result = {}
    for fact in sorted(facts, key=_fact_priority):
        text = fact_text(fact)
        if text not in result:
            result[text] = fact_kind(fact)
    return sorted(result.items(), key=lambda item: _RANK[item[1]])


def ahead_rows(context: dict, state: dict) -> list[dict]:
    """Rows for look-ahead days that the composed school week does not cover.

    `context` must reach at least `LOOKAHEAD_DAYS` past the next school day.
    """
    now = _now(context)
    hero = dashboard_focus(now).school_date
    names = [child["name"] for child in state["hero"]["children"]]
    covered = {day["date"] for day in state.get("weekdays", [])}
    facts = [fact for fact in context.get("items", []) if _usable(fact)]
    rows = []
    for offset in range(1, LOOKAHEAD_DAYS + 1):
        day = (hero + timedelta(days=offset)).isoformat()
        if day in covered:
            continue
        on_day = [fact for fact in facts if fact["effective_date"] == day]
        if not on_day:
            continue
        children = []
        for name in names:
            lines = _lines([fact for fact in on_day if fact.get("child") == name])[:AHEAD_LINES]
            children.append({"name": name, "lines": [_fit(text, TEXT_LIMIT) for text, _ in lines],
                             "kinds": [kind for _, kind in lines]})
        shared = [{"text": _fit(text, TEXT_LIMIT), "kind": kind}
                  for text, kind in _lines([fact for fact in on_day if fact.get("child") not in names])
                  ][:AHEAD_SHARED]
        if any(child["lines"] for child in children) or shared:
            rows.append({"date": day, "children": children, "shared": shared})
    return rows


def _later_text(fact: dict) -> str:
    title = re.sub(r"\s+", " ", str(fact.get("title") or "")).strip()
    if fact.get("cancelled") and "aflyst" not in title.casefold():
        title += " aflyst"
    return _fit(title, LATER_TEXT_LIMIT)


def later_events(context: dict, state: dict) -> list[dict]:
    """Trips, parties, meetings and required deadlines after the look-ahead, two weeks out.

    One entry per thing: the same title on the same date for both children, or a
    fact for no particular child, is one `Begge` entry. A title already shown from
    the next school day on, or earlier in the list, is a repeat and left out.
    """
    now = _now(context)
    hero = dashboard_focus(now).school_date
    first = hero + timedelta(days=LOOKAHEAD_DAYS + 1)
    last = hero + timedelta(days=LATER_DAYS)
    names = [child["name"] for child in state["hero"]["children"]]
    # A weekly event (morning singing) already shown in the days ahead is not repeated.
    seen = {_later_text(fact).casefold() for fact in context.get("items", []) if _usable(fact)
            and hero <= date.fromisoformat(fact["effective_date"]) < first}
    grouped = {}
    for fact in sorted(context.get("items", []), key=lambda fact: str(fact.get("effective_date"))):
        if not _usable(fact):
            continue
        day = date.fromisoformat(fact["effective_date"])
        kind = fact_kind(fact)
        if not first <= day <= last or kind not in (*EVENT_KINDS, "deadline"):
            continue
        text = _later_text(fact)
        if not text or (text.casefold() in seen and (day, text.casefold()) not in grouped):
            continue
        if any(key[1] == text.casefold() and key[0] != day for key in grouped):
            continue
        key = (day, text.casefold())
        entry = grouped.setdefault(key, {"date": day.isoformat(), "kind": kind, "text": text,
                                         "who": set()})
        entry["who"].add(fact.get("child") if fact.get("child") in names else "Begge")
        if _RANK[kind] < _RANK[entry["kind"]]:
            entry["kind"] = kind
    result = []
    for entry in grouped.values():
        who = entry.pop("who")
        entry["who"] = next(iter(who)) if len(who) == 1 else "Begge"
        result.append(entry)
    order = {name: index for index, name in enumerate([*names, "Begge"])}
    result.sort(key=lambda entry: (entry["date"], order[entry["who"]], entry["text"]))
    return result[:LATER_LIMIT]


def attach_lookahead(state: dict, context: dict) -> dict:
    """A copy of a frame's state with `ahead` and `later_events` for its moment.

    The weekend cover and the children's day view do not use them, and schema 1
    states are left alone.
    """
    if state.get("schema_version") != 2 or state.get("weather") or state.get("day_view"):
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


def _bounded_text(value, limit: int, label: str) -> None:
    if not isinstance(value, str) or not value or len(value) > limit or any(ord(c) < 32 for c in value):
        raise ValueError(f"{label} must be bounded single-line text")


def validate_parents_board(data: dict, names: list[str]) -> None:
    """Allowlist `ahead`, `later_events` and `device`; called by the board's own `validate`."""
    if data.get("schema_version") != 2:
        raise ValueError("the parents' board fields need schema version 2")
    ahead = data.get("ahead", [])
    if not isinstance(ahead, list) or len(ahead) > LOOKAHEAD_DAYS:
        raise ValueError(f"at most {LOOKAHEAD_DAYS} look-ahead rows")
    for row in ahead:
        if not isinstance(row, dict) or set(row) != {"date", "children", "shared"}:
            raise ValueError("unknown or missing look-ahead fields")
        date.fromisoformat(row["date"])
        children = row["children"]
        if not isinstance(children, list) or [
                child.get("name") if isinstance(child, dict) else None for child in children] != names:
            raise ValueError("look-ahead children must match the hero")
        for child in children:
            if (set(child) != {"name", "lines", "kinds"} or not isinstance(child["lines"], list)
                    or not isinstance(child["kinds"], list) or len(child["lines"]) > AHEAD_LINES
                    or len(child["lines"]) != len(child["kinds"])):
                raise ValueError("a look-ahead child has at most five lines, each with its kind")
            for text in child["lines"]:
                _bounded_text(text, TEXT_LIMIT, "look-ahead line")
            if any(kind not in KINDS for kind in child["kinds"]):
                raise ValueError("unknown look-ahead kind")
        shared = row["shared"]
        if not isinstance(shared, list) or len(shared) > AHEAD_SHARED:
            raise ValueError("at most two shared look-ahead lines")
        for entry in shared:
            if not isinstance(entry, dict) or set(entry) != {"text", "kind"} or entry["kind"] not in KINDS:
                raise ValueError("a shared look-ahead line has a text and a known kind")
            _bounded_text(entry["text"], TEXT_LIMIT, "shared look-ahead line")
    later = data.get("later_events", [])
    if not isinstance(later, list) or len(later) > LATER_LIMIT:
        raise ValueError(f"at most {LATER_LIMIT} later events")
    for entry in later:
        if not isinstance(entry, dict) or set(entry) != {"date", "who", "kind", "text"}:
            raise ValueError("unknown or missing later-event fields")
        date.fromisoformat(entry["date"])
        if entry["who"] not in [*names, "Begge"] or entry["kind"] not in (*EVENT_KINDS, "deadline"):
            raise ValueError("a later event belongs to a child or both and is an event or a deadline")
        _bounded_text(entry["text"], LATER_TEXT_LIMIT, "later event")
    if "device" in data:
        device = data["device"]
        if (not isinstance(device, dict) or set(device) != {"battery_percent"}
                or type(device["battery_percent"]) is not int or not 0 <= device["battery_percent"] <= 100):
            raise ValueError("device carries only the battery percentage")
