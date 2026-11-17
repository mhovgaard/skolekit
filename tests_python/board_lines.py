"""Board lines and states (schema 3) for tests: a line is built from a fact of its kind."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from skoleklar.present import present

# A fact of each board kind, as the extractor types it.
FACTS = {
    "homework": {"type": "homework", "actionability": "required"},
    "bring": {"type": "bring", "actionability": "required"},
    "optional": {"type": "bring", "actionability": "recommended"},
    "deadline": {"type": "parent_action", "actionability": "required"},
    "time_change": {"type": "schedule_change", "moves_time": True},
    "change": {"type": "schedule_change"},
    "substitute": {"type": "substitute"},
    "trip": {"type": "event", "category": "trip"},
    "party": {"type": "event", "category": "party"},
    "meeting": {"type": "event", "category": "meeting"},
    "event": {"type": "event"},
    "info": {"type": "context"},
    "routine": {"type": "event", "recurring": True},
}


def line(text: str, kind: str = "info", *, day: date | str | None = None, ref: str = "fact-1",
         **fact) -> dict:
    """A board line of `kind` showing one fact; extra keywords are the fact's typed fields."""
    if isinstance(day, str):
        day = date.fromisoformat(day)
    if kind == "more":
        return {"text": text, "refs": [], **present([], day=None)}
    return {"text": text, "refs": [ref], **present([{**FACTS[kind], **fact}], day=day)}


def freshness(composed: str = "2026-09-21T08:00:00+02:00") -> dict:
    return {"source_updated_at": composed, "composed_at": composed,
            "expires_at": (datetime.fromisoformat(composed) + timedelta(hours=36)).isoformat(),
            "status": "fresh"}


DAY_LABELS = ("Man.", "Tir.", "Ons.", "Tor.", "Fre.")


def week_state(monday: str = "2026-09-21", *, hero: str | None = None,
               composed: str | None = None, names=("ChildA", "ChildB")) -> dict:
    """An empty composed school week; `hero` is its hero date (default Monday)."""
    first = date.fromisoformat(monday)
    hero_day = date.fromisoformat(hero) if hero else first
    days = [first + timedelta(days=offset) for offset in range(5)]
    return {
        "schema_version": 3, "week_label": f"UGE {first.isocalendar().week}",
        "updated_label": "DATA MAN. 21. SEP. · 08.00",
        "freshness": freshness(composed or f"{hero_day.isoformat()}T08:00:00+02:00"),
        "hero": {"state": "I dag", "day": f"Mandag {hero_day.day}. sep.", "date": hero_day.isoformat(),
                 "children": [{"name": name, "lines": []} for name in names]},
        "weekdays": [{"date": day.isoformat(), "day": f"{DAY_LABELS[day.weekday()]} {day.day}. sep.",
                      "children": [{"name": name, "lines": []} for name in names], "shared": []}
                     for day in days],
        "weekend": [],
        "messages": {"checked": True, "bodies_retained": 0, "unread_count": 7,
                     "items": [{"participant": "Teacher T.", "children": [names[0]],
                                "activity": "21/09", "unread": True}]},
    }
