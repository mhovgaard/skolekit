"""The calendar: activities, school events and lessons, read in four-week chunks."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from skolekit.parsers.weekly_plan import content_to_text
from skolekit.profile import Child
from skolekit.records import SourceCheck, SourceRecord, calendar_source_key
from skolekit.sources.base import CollectionError, Scope, Source, authenticated_get

# The calendar's feeds: activities, school events, and the lessons (the timetable).
FEEDS = ("ActivitiesEvents", "SchoolEvents", "LessonsEvents")


def school_year_bounds(now: datetime | None = None) -> tuple[datetime, datetime]:
    current = now or datetime.now(UTC)
    start_year = current.year if current.month >= 8 else current.year - 1
    return datetime(start_year, 8, 1, tzinfo=UTC), datetime(
        start_year + 1, 8, 1, tzinfo=UTC
    )


def recent_calendar_bounds(
    scope: Scope, now: datetime | None = None
) -> tuple[datetime, datetime]:
    """From Monday of the current week, `weeks_back` before it to `weeks_ahead` after it."""
    zone = ZoneInfo("Europe/Copenhagen")
    current = (now or datetime.now(zone)).astimezone(zone)
    monday = (current - timedelta(days=current.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    start = monday - timedelta(weeks=scope.weeks_back)
    end = monday + timedelta(weeks=scope.weeks_ahead + 1)
    return start, end


CALENDAR_CONTENT_KEYS = (
    "title",
    "description",
    "location",
    "authorName",
    "invitees",
    "lessonName",
    "lessonClassName",
    "staffName",
    "absentStaff",
    "substituteStaff",
    "substitutionExtraTitle",
    "substituteExplainedAbbreviations",
    "Attachments",
    "IsPrivate",
)


def _calendar_record(
    child: Child, kind: str, endpoint: str, raw: dict[str, Any]
) -> SourceRecord:
    description = raw.get("description") or ""
    dates = [
        {
            "kind": "event",
            "start": raw.get("start") or raw.get("startDate"),
            "end": raw.get("end") or raw.get("endDate"),
            "all_day": bool(raw.get("allDay")),
        }
    ]
    meaningful = {key: raw.get(key) for key in CALENDAR_CONTENT_KEYS if key in raw}
    attachments = raw.get("Attachments") or []
    return SourceRecord(
        student_id=child.student_id,
        child=child.child,
        source_type="calendar",
        source_key=calendar_source_key(kind, raw),
        source_url=endpoint,
        title=raw.get("title") or raw.get("formattedName") or "",
        body_html=description,
        body_text=content_to_text(description),
        dates=dates,
        attachments=attachments if isinstance(attachments, list) else [attachments],
        links=[
            value
            for value in [raw.get("conversationLink"), raw.get("substitutionPageLink")]
            if value
        ],
        source_data={"calendar_kind": kind, "event": raw},
        content_data=meaningful,
        state_data={"calendar_kind": kind},
        raw_payload=raw,
    )


def collect_calendar(
    client: httpx.Client,
    child: Child,
    *,
    scope: Scope | None = None,
    now: datetime | None = None,
    kinds: tuple[str, ...] = FEEDS,
) -> SourceCheck:
    """Each feed in `kinds` for the scope's weeks, read in four-week chunks."""
    scope = scope or Scope()
    start, end = (
        school_year_bounds(now)
        if scope.school_year
        else recent_calendar_bounds(scope, now)
    )
    records: list[SourceRecord] = []
    counts: dict[str, int] = {}
    requests = 0
    for kind in kinds:
        endpoint = child.route(f"calendareventsource/{kind}")
        cursor = start
        kind_records: dict[str, SourceRecord] = {}
        while cursor < end:
            chunk_end = min(cursor + timedelta(days=28), end)
            params: dict[str, Any] = {
                "departmentIds": json.dumps(list(child.department_ids)),
                "start": int(cursor.timestamp()),
                "end": int(chunk_end.timestamp()),
            }
            if kind == "LessonsEvents":
                params["className"] = child.calendar_class_name
            response = authenticated_get(client, endpoint, params=params)
            requests += 1
            payload = response.json()
            if not isinstance(payload, list):
                raise CollectionError(f"Calendar {kind} response was not a list")
            for raw in payload:
                if not isinstance(raw, dict):
                    continue
                record = _calendar_record(child, kind, endpoint, raw)
                previous = kind_records.get(record.source_key)
                if previous is not None:
                    previous_hashes = previous.hashes()
                    current_hashes = record.hashes()
                    if (
                        previous_hashes["content_hash"]
                        != current_hashes["content_hash"]
                        or previous_hashes["state_hash"]
                        != current_hashes["state_hash"]
                    ):
                        raise CollectionError(
                            f"Calendar identity collision for {record.source_key}"
                        )
                    continue
                kind_records[record.source_key] = record
            cursor = chunk_end
        counts[kind] = len(kind_records)
        records.extend(kind_records.values())
    return SourceCheck(
        student_id=child.student_id,
        child=child.child,
        source_type="calendar",
        records=records,
        metadata={
            "counts": counts,
            "range_start": start.isoformat(),
            "range_end": end.isoformat(),
            "requests": requests,
        },
        coverage={
            "kind": "date_window",
            "start": start.date().isoformat(),
            "end": end.date().isoformat(),
        },
    )



def _collect(client: httpx.Client, child: Child, scope: Scope) -> SourceCheck:
    return collect_calendar(client, child, scope=scope)


SOURCE = Source("calendar", _collect)
