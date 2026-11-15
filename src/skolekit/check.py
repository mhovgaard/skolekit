"""How well a school's rules fit what its pages say: counts per rule, never text.

For each rule of the school's wording it counts the items it read (matched),
the items it found nothing in (unmatched), and the items where it found its
words but could not read the value (refused), such as a date that doesn't
exist. The live check (`skolekit school check`) feeds it what the site shows
now; a product can feed it what it stored. Its output holds counts only, so it
is safe to share when a second school is brought on.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from typing import Any

from skolekit.parsers.weekly_plan import segment_content
from skolekit.wording import Wording

_FORM_RULES = ("booked", "answered")
_NOTICE_RULES = ("signup", "expires", "days", "minutes")


def _tally() -> dict[str, int]:
    return {"matched": 0, "unmatched": 0, "refused": 0}


def _count(tally: dict[str, int], found: bool, value: Any) -> None:
    if not found:
        tally["unmatched"] += 1
    elif value is None or value == [] or (isinstance(value, tuple) and None in value):
        tally["refused"] += 1
    else:
        tally["matched"] += 1


def form_status(payload: dict[str, Any]) -> str:
    """A form's status line, from its listing or its state."""
    listing = (payload.get("raw_payload") or {}).get("list_item") or {}
    return str(listing.get("Status") or (payload.get("state_data") or {}).get("status") or "")


def plan_lessons(plan: dict[str, Any]) -> list[str]:
    """The text of every lesson in a parsed weekly plan."""
    lessons = [*(plan.get("general_plans") or []),
               *(lesson for day in plan.get("days") or [] for lesson in day.get("plans") or [])]
    return [str(lesson.get("content_text") or "") for lesson in lessons]


def count_rules(wording: Wording, *, statuses: Iterable[str], notice_texts: Iterable[str],
                broad_texts: Iterable[str], lesson_texts: Iterable[str]) -> dict[str, Any]:
    """Per rule, how many of the given items it matched, did not match, and refused."""
    rules: dict[str, Any] = {}
    statuses, notices = list(statuses), list(notice_texts)
    for name in _FORM_RULES:
        tally = rules.setdefault(f"forms.{name}", _tally())
        read = wording.booked_time if name == "booked" else wording.answered_on
        for status in statuses:
            _count(tally, bool(wording.rule(name).search(status)), read(status))
    readers = {"signup": wording.signup_window, "expires": wording.expires_on,
               "days": wording.notice_days, "minutes": wording.notice_minutes}
    for name in _NOTICE_RULES:
        tally = rules.setdefault(f"notices.{name}", _tally())
        for body in notices:
            _count(tally, bool(wording.rule(name).search(body)), readers[name](body))
    broad = rules.setdefault("classes.broad", _tally())
    for text in broad_texts:
        broad["matched" if wording.names_several_classes(text) else "unmatched"] += 1
    sections: dict[str, Any] = {**_tally(), "kinds": Counter()}
    for text in lesson_texts:
        if not text:
            continue
        known = [segment["section_hint"] for segment in segment_content(text, wording)
                 if segment["section_hint"] != "unsectioned"]
        sections["matched" if known else "unmatched"] += 1
        sections["kinds"].update(known)
    rules["weekly_plan.sections"] = {**sections, "kinds": dict(sorted(sections["kinds"].items()))}
    return rules
