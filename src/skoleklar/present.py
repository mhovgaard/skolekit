"""How a board line looks, from the facts it shows: one function for every view.

A line of the board state (schema 3) is its wording and the facts it shows, by
reference. `present` turns those facts into everything that guides the eye: the
homework checkbox and its bold subject, the bold `Medbring`, `Valgfrit`, `Frist`,
`Vikar` and `Aflyst` labels, the `TUR`/`FEST`/`MØDE`/`ARR.` boxes, the yellow
time-urgency marker and whether the line is a weekly routine the board leaves
out. The panel, the browser, the children's view and the weekend cover draw what
it returned and never read the wording. See docs/parents-board.md.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from skoleklar import fact as fact_model

CHIPS = {"trip": "TUR", "party": "FEST", "meeting": "MØDE", "event": "ARR."}
LABELS = {"bring": "Medbring", "optional": "Valgfrit", "deadline": "Frist", "substitute": "Vikar"}
CANCELLED_LABEL = "Aflyst"
# Yellow marks time urgency only: a deadline, or a change that moves a time, within
# this many days of the frame.
URGENT_KINDS = frozenset({"deadline", "time_change"})
URGENT_DAYS = 2
# A homework line's bold subject, in the board's short form.
SHORT_SUBJECTS = {
    "natur/teknologi": "N/T", "natur og teknologi": "N/T", "n/t": "N/T", "matematik": "Mat.",
    "mat.": "Mat.", "mat": "Mat.", "engelsk": "Eng.", "eng.": "Eng.", "kristendomskundskab": "Kristendom",
    "håndværk og design": "Håndværk",
}
SUBJECT_LIMIT = 16
# What a line object holds: its wording and facts, then its presentation.
LINE_FIELDS = ("text", "refs", "kind", "label", "chip", "box", "subject", "cancelled", "hidden",
               "rain_gear", "retold", "urgent")


def short_subject(name: Any) -> str:
    """'Mat.' for 'Matematik'; a subject without a short form is kept, when short."""
    if not isinstance(name, str) or not name.strip():
        return ""
    name = " ".join(name.split())
    short = SHORT_SUBJECTS.get(name.casefold(), name)
    return short if len(short) <= SUBJECT_LIMIT else ""


def present(facts: list[dict[str, Any]], *, day: date | None) -> dict[str, Any]:
    """The presentation of a line that shows `facts` on `day`.

    - `kind`: what the line is (`skoleklar.fact.line_kind`);
    - `box`: the homework checkbox, with the bold `subject` beside it;
    - `label`: the bold word before the text (`Medbring`, `Aflyst` …), or '';
    - `chip`: the blue category box (`TUR` …), or '';
    - `cancelled`: every fact behind the line is cancelled;
    - `hidden`: a weekly routine, which the board leaves out;
    - `rain_gear`: the line's things to bring are rain clothes;
    - `retold`: the day's plan headline already retells one of its facts;
    - `urgent`: the dates (`from`, `to`) on which the line is yellow, or None.
    """
    kind = fact_model.line_kind(facts) if facts else "more"
    cancelled = bool(facts) and all(fact.get("cancelled") for fact in facts)
    subjects = {short_subject(fact.get("school_subject")) for fact in facts
                if fact_model.kind(fact) == "homework"} - {""}
    urgent = None
    if kind in URGENT_KINDS and day is not None and not cancelled:
        urgent = {"from": (day - timedelta(days=URGENT_DAYS)).isoformat(), "to": day.isoformat()}
    return {
        "kind": kind,
        "label": CANCELLED_LABEL if cancelled else LABELS.get(kind, ""),
        "chip": CHIPS.get(kind, ""),
        "box": kind == "homework",
        "subject": subjects.pop() if kind == "homework" and len(subjects) == 1 else "",
        "cancelled": cancelled,
        "hidden": kind == "routine",
        "rain_gear": any(fact.get("rain_gear") is True for fact in facts),
        "retold": any(fact.get("retold") is True for fact in facts),
        "urgent": urgent,
    }


def line(text: str, refs: list[str], facts: list[dict[str, Any]], *, day: date | None) -> dict[str, Any]:
    """A board line: its wording, the facts it shows and how it looks."""
    return {"text": text, "refs": list(refs), **present(facts, day=day)}


def more_line(count: int) -> dict[str, Any]:
    """The `+N punkter mere` line for what a row had no room for."""
    return line(f"+{count} {'punkt' if count == 1 else 'punkter'} mere", [], [], day=None)


# How a line reads in the children's remember list, in a child's words; None leaves
# it out (a substitute is on its lesson tile, a routine needs no reminder).
REMEMBER = {"optional": "{} (hvis du vil)", "homework": "Lektie: {}", "substitute": None,
            "routine": None, "more": None}


def remember(entry: dict[str, Any]) -> str:
    """A line as the children's view says it: from its presentation, its words kept whole."""
    form = REMEMBER.get(entry["kind"], "{}")
    if form is None or entry["hidden"]:
        return ""
    if entry["kind"] == "homework" and entry["subject"]:
        form = form.replace("{}", entry["subject"] + " {}")
    text = form.format(" ".join(entry["text"].split()))
    if entry["cancelled"]:
        text += " er aflyst"
    return text[:1].upper() + text[1:]


def urgent_on(entry: dict[str, Any], today: date | None) -> bool:
    """Whether a line is yellow on `today`: inside the window `present` gave it."""
    window = entry.get("urgent")
    return bool(window and today and window["from"] <= today.isoformat() <= window["to"])


def validate_line(value: Any, *, limit: int = 100) -> None:
    """Allowlist one line object of the board state."""
    if not isinstance(value, dict) or set(value) != set(LINE_FIELDS):
        raise ValueError("a board line has its text, refs and presentation")
    text = value["text"]
    if not isinstance(text, str) or not text or len(text) > limit or any(ord(c) < 32 for c in text):
        raise ValueError("a board line's text is bounded single-line text")
    refs = value["refs"]
    if not isinstance(refs, list) or len(refs) > 12 or any(
            not isinstance(ref, str) or not ref.startswith("fact-") or len(ref) > 12 for ref in refs):
        raise ValueError("a board line's refs are fact references")
    if value["kind"] not in fact_model.KINDS:
        raise ValueError("unknown board line kind")
    if value["label"] not in {"", CANCELLED_LABEL, *LABELS.values()} or value["chip"] not in {"", *CHIPS.values()}:
        raise ValueError("unknown board line label or box")
    subject = value["subject"]
    if not isinstance(subject, str) or len(subject) > SUBJECT_LIMIT or any(ord(c) < 32 for c in subject):
        raise ValueError("a board line's subject is a short word")
    if any(type(value[key]) is not bool for key in ("box", "cancelled", "hidden", "rain_gear", "retold")):
        raise ValueError("a board line's flags are booleans")
    urgent = value["urgent"]
    if urgent is not None:
        if not isinstance(urgent, dict) or set(urgent) != {"from", "to"}:
            raise ValueError("a board line's urgency is a window of dates")
        if date.fromisoformat(urgent["from"]) > date.fromisoformat(urgent["to"]):
            raise ValueError("a board line's urgency window runs forward")
