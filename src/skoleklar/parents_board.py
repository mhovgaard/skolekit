"""Data for the parents' board, written by code: no model calls.

The parents' board gives each child a row across the panel. The left (dark) side
is the next school day; the right side looks four school days ahead, so the board
always covers the next five school days, then lists events up to two weeks out.
This module supplies what the composed week does not carry:

- `kinds`: what each board line is (homework, a trip, a deadline, a change that
  moves a time, a weekly routine …), from the facts behind it, so the renderer can
  draw a checkbox, a category box or the yellow time-urgency marker without parsing
  Danish text. A fact derived with derive-v10 says what it is (`category`,
  `moves_time`, `recurring`); only older facts are read from their title's words;
- `ahead`: the look-ahead days the composed school week does not cover (the next
  school week, and a weekend day in between that has something on), written from
  the facts like the fallback board;
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

KINDS = ("homework", "bring", "optional", "deadline", "time_change", "change", "substitute",
         "trip", "party", "meeting", "event", "info", "routine", "more")
EVENT_KINDS = ("trip", "party", "meeting", "event")
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
# Lower rank wins when one line joins facts of several kinds.
# A routine ranks last, so a line that joins it with anything else is never hidden.
_RANK = {"homework": 0, "trip": 1, "party": 1, "meeting": 1, "event": 1, "deadline": 2,
         "time_change": 3, "change": 4, "bring": 5, "optional": 6, "substitute": 7, "info": 8,
         "more": 9, "routine": 10}
_CATEGORY_KINDS = {"trip": "trip", "party": "party", "meeting": "meeting", "other": "event"}
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
# Only for facts from before derive-v10, which do not say these things themselves:
# a change that moves a time, and the school's weekly routines.
_TIMED = re.compile(r"\d{1,2}[.:]\d{2}|\bkl\.?\s*\d|mødetid|\bfri\b|hentes|tidlig|senere|forsink",
                    re.IGNORECASE)
ROUTINES = re.compile(r"^(morgensang|fællessang)\b", re.IGNORECASE)


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


def _structured(fact: dict) -> bool:
    """A fact derived with derive-v10: it always carries `recurring` (a null field is
    left out on its way to the board, a boolean never is)."""
    return isinstance(fact.get("recurring"), bool)


def fact_kind(fact: dict) -> str:
    """What one fact is on the board.

    A fact derived with derive-v10 says it (`category`, `moves_time`, `recurring`)
    and is taken at its word, a null as much as a value. Only a fact from before
    v10 is read from its title's words, as the board always did.
    """
    kind, actionability = fact.get("type"), fact.get("actionability")
    title = str(fact.get("title") or "")
    structured = _structured(fact)
    if kind == "homework":
        return "homework"
    if kind == "bring":
        return "optional" if actionability == "recommended" else "bring"
    if kind == "parent_action":
        if fact.get("category") == "meeting" or (
                not structured and _MEETING_WORDS.search(title) and not _SIGN_UP.search(title)):
            return "meeting"
        return "optional" if actionability == "recommended" else "deadline"
    if kind == "schedule_change":
        moves = fact.get("moves_time")
        moves = moves if isinstance(moves, bool) else not structured and bool(_TIMED.search(title))
        return "time_change" if moves else "change"
    if kind == "substitute":
        return "substitute"
    if kind == "event":
        if not fact.get("cancelled") and (fact.get("recurring") is True or (
                not structured and ROUTINES.match(title.strip()) and "aflyst" not in title.casefold())):
            return "routine"
        # The owner's confirmation, then a message's category, then the derivation's.
        activity = fact.get("confirmed_activity_kind") or fact.get("activity_kind")
        if activity in _ACTIVITY_KINDS and not _MEETING_WORDS.search(title):
            return _ACTIVITY_KINDS[activity]
        if fact.get("category") in _CATEGORY_KINDS:
            return _CATEGORY_KINDS[fact["category"]]
        return "event" if structured else event_kind(title)
    return "info"


def _line_words(kind: str, text: str, members: list[dict]) -> str:
    """A line's kind, read from its own words where its facts do not say it.

    Only for facts from before derive-v10: the composed line can say more than
    their titles ('Skolehjemssamtaler', 'Fri kl. 12', 'Morgensang').
    """
    if any(_structured(member) for member in members):
        return kind
    words = re.sub(r"^besked:\s*", "", str(text).strip(), flags=re.IGNORECASE)
    if kind in EVENT_KINDS and ROUTINES.match(words) and "aflyst" not in words.casefold():
        return "routine"
    if kind == "event":
        return event_kind(words)
    if kind == "change" and _TIMED.search(words):
        return "time_change"
    return kind


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
            result.append(_line_words(line_kind(members), text, members) if members else text_kind(text))
        return result

    for c, child in enumerate(state["hero"]["children"]):
        child["kinds"] = kinds(f"hero.children[{c}]", child["lines"])
    for d, day in enumerate(state.get("weekdays", [])):
        for c, child in enumerate(day["children"]):
            child["kinds"] = kinds(f"weekdays[{d}].children[{c}]", child["lines"])
        if day.get("shared"):
            members = by_location.get(f"weekdays[{d}].shared")
            day["shared_kind"] = (_line_words(line_kind(members), day["shared"], members) if members
                                  else text_kind(day["shared"]))
        else:
            day.pop("shared_kind", None)
    return state


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


def _lines(facts: list[dict]) -> list[tuple[str, str]]:
    """(text, kind) per distinct fact text, most important first, as the fallback board writes them.

    A weekly routine is left out, so it never takes one of a day's few lines.
    """
    result = {}
    for fact in sorted(facts, key=_fact_priority):
        text = fact_text(fact)
        if text not in result:
            result[text] = fact_kind(fact)
    return sorted(((text, kind) for text, kind in result.items() if kind != "routine"),
                  key=lambda item: _RANK[item[1]])


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
