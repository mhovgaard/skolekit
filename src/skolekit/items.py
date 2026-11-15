"""What one source item holds, read without memory, a database or a model.

The live CLI/MCP and a product's typed tables share these per-item readers.
Each reads source fields with configured wording, without cross-item memory
or model interpretation.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any
from urllib.parse import urlparse

from skolekit.wording import Wording

# The form's link, open or closed, with or without '/edit' while the round is open.
FORM_PATH = re.compile(r"/forms/(?:open|closed)/(?P<kind>[a-z]+)/(?P<id>\d+)(?:/edit)?/?$")
_FORM_KEY = re.compile(r"[a-z]+:\d+")
_LOCAL_TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")
# '2. klasse', '1 & 2 klasse', '0.-kl.' style mentions in an event's title or text.
_CLASS_MENTION = re.compile(r"\b(\d{1,2})(?:\.?\s*(?:&|og)\s*(\d{1,2}))?\.?\s*(?:kl\b\.?|klasse)", re.I)
_CLASS_CODE = re.compile(r"\d{2}")
# The calendar feeds that hold events; the lessons feed is the timetable.
EVENT_FEEDS = ("SchoolEvents", "ActivitiesEvents")


def form_key(source_key: str, source_url: str) -> str | None:
    """One key per form for its whole life, whatever its link does: `conversations:344`."""
    match = FORM_PATH.search(urlparse(source_url or "").path)
    if match:
        return f"{match.group('kind')}:{match.group('id')}"
    if _FORM_KEY.fullmatch(source_key or ""):
        return source_key
    match = FORM_PATH.search(urlparse(source_key or "").path)
    return f"{match.group('kind')}:{match.group('id')}" if match else None


def class_levels(title: str, text: str, invitees: Iterable[Any]) -> set[int]:
    """The class levels an event names: its invited class codes and the classes its text mentions."""
    levels = {int(code) for code in (str(value) for value in invitees or [])
              if _CLASS_CODE.fullmatch(code)}
    for match in _CLASS_MENTION.finditer(f"{title} {text}"):
        levels |= {int(group) for group in match.groups() if group}
    return {level for level in levels if 0 <= level <= 10}


def local_time(value: Any) -> str | None:
    """`2026-11-09T14:00` from the site's longer timestamps, or None."""
    text = str(value or "")[:16]
    return text if _LOCAL_TIME.fullmatch(text) else None


def read_form(payload: dict[str, Any], *, source_key: str, source_url: str, title: str | None,
              wording: Wording) -> dict[str, Any] | None:
    """A form's key, state, status and the dates it holds; None when it has no key."""
    key = form_key(source_key, source_url)
    if key is None:
        return None
    listing = (payload.get("raw_payload") or {}).get("list_item") or {}
    state = (payload.get("state_data") or {}).get("list_state")
    if state not in ("open", "closed"):
        state = "closed" if "/forms/closed/" in (source_url or "") else "open"
    status = str(listing.get("Status") or (payload.get("state_data") or {}).get("status") or "")
    return {
        "form_key": key,
        "type_label": str(listing.get("Type") or (payload.get("source_data") or {}).get("type") or ""),
        "title": str(listing.get("Name") or title or ""),
        "list_state": state,
        "status_text": status,
        "booked_local": wording.booked_time(status),
        "answered_on": wording.answered_on(status),
        "deadline": wording.read_date(str(listing.get("Deadline") or "")),
    }


def read_event(payload: dict[str, Any], *, source_key: str, title: str | None,
               body_text: str | None) -> dict[str, Any] | None:
    """A school or activities event; None for any other feed. `event_id` is None when unreadable."""
    source = payload.get("source_data") or {}
    feed = source.get("calendar_kind")
    if feed not in EVENT_FEEDS:
        return None
    event = source.get("event") or {}
    raw_id = event.get("originalId") or (source_key or "").split(":", 1)[-1]
    try:
        event_id: int | None = int(raw_id)
    except (TypeError, ValueError):
        event_id = None
    heading = str(event.get("title") or title or "").strip()
    text = str(body_text or "")
    starts = local_time(event.get("start") or event.get("startDate"))
    return {
        "feed": feed,
        "event_id": event_id,
        "title": heading,
        "description": text,
        "class_levels": sorted(class_levels(heading, text, event.get("invitees") or [])),
        # A list of class codes on the live site; some events name one group as text.
        "invitees": ([invitees] if isinstance(invitees := event.get("invitees"), str)
                     else [str(value) for value in invitees or [] if value]),
        "starts_local": starts,
        "ends_local": local_time(event.get("end") or event.get("endDate")) or starts,
        "all_day": bool(event.get("allDay")),
    }


def read_notice(body_text: str | None, *, title: str | None, wording: Wording) -> dict[str, Any]:
    """A front-page notice's title and the dates and length its text states."""
    body = str(body_text or "")
    signup_from, signup_until = wording.signup_window(body)
    return {
        "title": str(title or body.split("\n", 1)[0])[:200],
        "signup_from": signup_from,
        "signup_until": signup_until,
        "expires_on": wording.expires_on(body),
        "minutes": wording.notice_minutes(body),
        "days": wording.notice_days(body),
    }


# Fields the site sets when a lesson is cancelled, depending on its version.
_CANCELLED_FIELDS = ("cancelled", "canceled", "isCancelled", "isCanceled")
_NO_CLASS = {"absent", "present", "none", "null"}


def read_lesson(raw: dict[str, Any], *, wording: Wording) -> dict[str, Any]:
    """One timetable slot: when, what, who teaches it, and whether it changed.

    SkoleIntra also fills `substituteStaff` with the scheduled teacher while
    decorating the slot as "Vikar for <same teacher>". That is the ordinary
    teacher, so such a slot counts as unchanged.
    """
    substitute = str(raw.get("substituteStaff") or "").strip()
    teacher = str(raw.get("staffName") or "").strip()
    same_teacher = bool(substitute and teacher and substitute.casefold() == teacher.casefold())
    cancelled = any(raw.get(field) for field in _CANCELLED_FIELDS) or wording.lesson_is_cancelled(
        str(raw.get("title") or ""))
    changed = not same_teacher and bool(
        cancelled
        or substitute
        or raw.get("className")
        or raw.get("absentStaff")
        or raw.get("substituteExplainedAbbreviations")
        or raw.get("substitutionPageLink")
    )
    class_name = str(raw.get("lessonClassName") or raw.get("className") or "").strip()
    if class_name.casefold() in _NO_CLASS:
        class_name = ""
    return {
        "starts_local": local_time(raw.get("start")),
        "ends_local": local_time(raw.get("end")),
        "subject": str(raw.get("lessonName") or raw.get("title") or "").strip(),
        "class": class_name,
        "location": str(raw.get("location") or ""),
        "teacher": teacher,
        "substitute": substitute if substitute and not same_teacher else None,
        "absent": raw.get("absentStaff") or None,
        "note": raw.get("substitutionExtraTitle") or None,
        "cancelled": bool(cancelled),
        "changed": changed,
    }
