from __future__ import annotations

from collections import defaultdict
from typing import Any


def normalize_lesson_event(
    raw: dict[str, Any],
    *,
    child: str,
    student_id: int,
    source_url: str,
) -> dict[str, Any]:
    substitute_teacher = (raw.get("substituteStaff") or "").strip()
    scheduled_teacher = (raw.get("staffName") or "").strip()
    start = raw.get("start") or raw.get("startDate")
    end = raw.get("end") or raw.get("endDate")
    return {
        "source_event_id": raw.get("id"),
        "source_original_id": raw.get("originalId"),
        "source_url": source_url,
        "student_id": student_id,
        "child": child,
        "start": start,
        "end": end,
        "date": start[:10] if isinstance(start, str) else None,
        "subject": raw.get("lessonName") or raw.get("title"),
        "title": raw.get("title"),
        "location": raw.get("location") or [],
        "scheduled_teacher": scheduled_teacher,
        "substitute_teacher": substitute_teacher,
        "absent_teacher_label": raw.get("absentStaff") or "",
        "substitution_label": raw.get("substitutionExtraTitle") or "",
        "substitution_details": raw.get("substituteExplainedAbbreviations") or [],
        "is_substitution": bool(substitute_teacher),
        "change_kind": "substitute" if substitute_teacher else None,
    }


def group_substitutions(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        if not event.get("is_substitution"):
            continue
        key = (
            event.get("student_id"),
            event.get("child"),
            event.get("date"),
            event.get("scheduled_teacher"),
            event.get("substitute_teacher"),
        )
        grouped[key].append(event)

    results: list[dict[str, Any]] = []
    for key, lesson_events in grouped.items():
        lesson_events.sort(key=lambda item: item.get("start") or "")
        subjects = list(
            dict.fromkeys(
                event["subject"] for event in lesson_events if event.get("subject")
            )
        )
        locations = list(
            dict.fromkeys(
                location
                for event in lesson_events
                for location in event.get("location") or []
                if location
            )
        )
        results.append(
            {
                "stream": "calendar",
                "type": "schedule_change",
                "change_kind": "substitute",
                "student_id": key[0],
                "child": key[1],
                "date": key[2],
                "scheduled_teacher": key[3],
                "substitute_teacher": key[4],
                "start": min(
                    event["start"] for event in lesson_events if event.get("start")
                ),
                "end": max(
                    event["end"] for event in lesson_events if event.get("end")
                ),
                "subjects": subjects,
                "locations": locations,
                "lesson_count": len(lesson_events),
                "source_event_ids": [
                    event["source_event_id"]
                    for event in lesson_events
                    if event.get("source_event_id")
                ],
                "source_url": lesson_events[0]["source_url"],
            }
        )
    return sorted(
        results,
        key=lambda item: (
            item.get("date") or "",
            item.get("start") or "",
            item.get("child") or "",
        ),
    )
