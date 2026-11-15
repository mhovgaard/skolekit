"""Messages as a parent reads them live: shortened names and the inbox's dates.

Live reads use shortened names and current inbox dates. Restricted
collection, opaque handles and extraction state belong to the product.
Message text is never stored by the toolkit.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, timedelta
from typing import Any

from skolekit.wording import DANISH


_PARENTHETICAL = re.compile(r"\s*\([^)]*\)\s*$")
_PARTICIPANT_SEPARATOR = re.compile(r"\s*(?:,|;|\s+&\s+|\s+og\s+)\s*", re.I)


class MessagePrivacyError(RuntimeError):
    """A content-free failure in the restricted message boundary."""


def _first_grapheme(value: str) -> str:
    if not value:
        return ""
    result = value[0]
    for character in value[1:]:
        if not unicodedata.combining(character):
            break
        result += character
    return result.upper()


def shorten_name(value: str | None) -> str:
    """Return a deterministic first-name plus surname-initial display label."""
    cleaned = _PARENTHETICAL.sub("", str(value or "")).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        return ""
    tokens = cleaned.split(" ")
    if len(tokens) == 1:
        return tokens[0]
    initial = _first_grapheme(tokens[-1].lstrip("-"))
    return f"{tokens[0]} {initial}." if initial else tokens[0]


def shorten_participants(value: str | None) -> list[str]:
    participants = [
        shorten_name(part)
        for part in _PARTICIPANT_SEPARATOR.split(str(value or "").strip())
    ]
    return list(dict.fromkeys(part for part in participants if part))


def normalize_latest_activity(
    value: str | None, *, observed_on: date | None = None
) -> str | None:
    """Normalize the inbox's Danish display date without inventing precision."""
    text = re.sub(r"\s+", " ", str(value or "")).strip().casefold()
    if not text:
        return None
    reference = observed_on or datetime.now().astimezone().date()
    time_match = re.search(r"\b(\d{1,2}):(\d{2})\b", text)
    hour = int(time_match.group(1)) if time_match else None
    minute = int(time_match.group(2)) if time_match else None
    if hour is not None and (hour > 23 or minute is None or minute > 59):
        return None

    activity_date: date | None = None
    iso_match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", text)
    if iso_match:
        try:
            activity_date = date.fromisoformat(iso_match.group(1))
        except ValueError:
            return None
    elif DANISH.yesterday in text:
        activity_date = reference - timedelta(days=1)
    elif DANISH.today in text:
        activity_date = reference
    else:
        danish_match = re.search(
            r"\b(\d{1,2})\.\s*([a-zæøå]{3,})\.?"
            r"(?:\s+(\d{4}))?\b",
            text,
        )
        if danish_match:
            month = DANISH.month(danish_match.group(2))
            if month is None:
                return None
            year = int(danish_match.group(3) or reference.year)
            try:
                activity_date = date(year, month, int(danish_match.group(1)))
            except ValueError:
                return None
            if danish_match.group(3) is None and activity_date > reference:
                activity_date = activity_date.replace(year=year - 1)
        elif time_match:
            activity_date = reference

    if activity_date is None:
        return None
    if hour is None:
        return activity_date.isoformat()
    return datetime.combine(
        activity_date, datetime.min.time().replace(hour=hour, minute=minute)
    ).isoformat(timespec="minutes")


def conversation_source_id(conversation: dict[str, Any]) -> tuple[str, Any]:
    thread_id = conversation.get("thread_id")
    if thread_id is not None:
        return "thread", thread_id
    message_id = conversation.get("latest_message_id")
    if message_id is None:
        raise MessagePrivacyError("Conversation has no resolvable identifier")
    return "threadless", message_id
