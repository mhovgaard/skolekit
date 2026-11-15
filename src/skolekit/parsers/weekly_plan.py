from __future__ import annotations

import hashlib
import json
import re
from typing import Any
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from skolekit.wording import DANISH, Wording


WEEKLY_PLAN_SELECTOR = "#root[data-clientlogic-settings-weeklyplansapp]"
DAY_TIMING_DEFAULTS = {
    "homework": "complete_before_source_day",
    "home_activity": "complete_before_source_day",
    "bring": "needed_on_source_day",
    "parent_action": "explicit_date_or_review",
    "schedule_event": "occurs_on_source_day",
    "schedule_change": "occurs_on_source_day",
    "routine": "occurs_on_source_day",
    "daily_program": "occurs_on_source_day",
    "lesson_preview": "occurs_on_source_day",
    "context": "no_derived_deadline",
    "future_notice": "no_derived_deadline",
}
# The first segment is the school's portal ([site] portal, 'parent' by default).
WEEKLY_PLAN_URL_PATTERN = re.compile(
    r"/[a-z]+/(?P<student_id>\d+)/(?P<child>[^/]+?)item/"
    r"weeklyplansandhomework/item/class/(?P<week>\d{1,2})-(?P<year>\d{4})$",
    flags=re.I,
)


class WeeklyPlanParseError(ValueError):
    """Raised when the expected embedded weekly-plan payload is unavailable."""


def content_to_text(value: str | None) -> str:
    if not value:
        return ""
    soup = BeautifulSoup(value, "html.parser")
    for break_tag in soup.find_all("br"):
        break_tag.replace_with("\n")
    lines = [" ".join(line.split()) for line in soup.get_text("\n").splitlines()]
    return "\n".join(line for line in lines if line)


def segment_content(text: str, wording: Wording = DANISH) -> list[dict[str, str]]:
    """The text split at the headings the school uses, each section with what it holds."""
    headings = wording.section_pattern()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    merged_lines: list[str] = []
    for line in lines:
        if line.startswith(":") and merged_lines:
            merged_lines[-1] = f"{merged_lines[-1]}{line}"
        else:
            merged_lines.append(line)

    segments: list[dict[str, str]] = []
    current_heading = ""
    current_hint = "unsectioned"
    current_lines: list[str] = []

    def flush() -> None:
        if not current_lines:
            return
        segments.append(
            {
                "section_hint": current_hint,
                "heading": current_heading,
                "text": "\n".join(current_lines),
            }
        )

    for line in merged_lines:
        match = headings.match(line)
        if match:
            flush()
            current_heading = match.group("heading")
            current_hint = wording.section_kind(current_heading)
            current_lines = []
            if match.group("remainder"):
                current_lines.append(match.group("remainder"))
        else:
            current_lines.append(line)
    flush()
    return segments


def normalize_lesson_plan(raw: dict[str, Any], wording: Wording = DANISH) -> dict[str, Any]:
    subject = raw.get("Subject") or {}
    content_html = raw.get("Content") or ""
    return {
        "source_plan_id": raw.get("WeeklyPlanId"),
        "subject": subject.get("Title") or subject.get("FormattedTitle") or "",
        "subject_is_general": bool(subject.get("IsGeneralSubject")),
        "lesson_number": subject.get("LessonNumber"),
        "content_html": content_html,
        "content_text": content_to_text(content_html),
        "segments": segment_content(content_to_text(content_html), wording),
        "link": raw.get("Link"),
        "attachments": raw.get("Attachments") or [],
        "snapshots": raw.get("Snapshots") or [],
        "is_draft": bool(raw.get("IsDraft")),
    }


def parse_weekly_plan_html(html: str, source_url: str, *, wording: Wording = DANISH) -> dict[str, Any]:
    match = WEEKLY_PLAN_URL_PATTERN.search(urlparse(source_url).path)
    if not match:
        raise WeeklyPlanParseError("URL is not a weekly-plan item route")

    soup = BeautifulSoup(html, "html.parser")
    root = soup.select_one(WEEKLY_PLAN_SELECTOR)
    if root is None:
        raise WeeklyPlanParseError("Weekly-plan settings element was not found")
    encoded_payload = root.get("data-clientlogic-settings-weeklyplansapp")
    if not encoded_payload:
        raise WeeklyPlanParseError("Weekly-plan settings payload was empty")

    try:
        payload = json.loads(encoded_payload)
        selected = payload["SelectedPlan"]
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise WeeklyPlanParseError("Weekly-plan settings payload was invalid") from error

    general_raw = selected.get("GeneralPlan") or {}
    days: list[dict[str, Any]] = []
    for day in selected.get("DailyPlans") or []:
        plans = [normalize_lesson_plan(plan, wording) for plan in day.get("LessonPlans") or []]
        days.append(
            {
                "weekday": day.get("Day"),
                "weekday_number": day.get("DayOfWeek"),
                "date": day.get("Date"),
                "formatted_date": day.get("FormattedDate"),
                "timing_defaults": DAY_TIMING_DEFAULTS,
                "plans": plans,
            }
        )

    normalized: dict[str, Any] = {
        "schema_version": 1,
        "source": {
            "url": source_url,
            "student_id": int(match.group("student_id")),
            "child": match.group("child"),
        },
        "week": {
            "number": int(match.group("week")),
            "year": int(match.group("year")),
            "label": selected.get("FormattedWeek"),
            "class_or_group": selected.get("ClassOrGroup"),
        },
        "general_plans": [
            normalize_lesson_plan(plan, wording)
            for plan in general_raw.get("LessonPlans") or []
        ],
        "days": days,
        "attachments": selected.get("Attachments") or [],
    }
    canonical = json.dumps(normalized, ensure_ascii=False, sort_keys=True).encode("utf-8")
    normalized["source_hash"] = hashlib.sha256(canonical).hexdigest()
    return normalized


def weekly_plan_metadata(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": plan["source"],
        "week": plan["week"],
        "general_plan_count": len(plan["general_plans"]),
        "general_content_characters": sum(
            len(item["content_text"]) for item in plan["general_plans"]
        ),
        "days": [
            {
                "weekday": day["weekday"],
                "date": day["date"],
                "timing_defaults": day["timing_defaults"],
                "plan_count": len(day["plans"]),
                "content_characters": sum(
                    len(item["content_text"]) for item in day["plans"]
                ),
            }
            for day in plan["days"]
        ],
        "source_hash": plan["source_hash"],
    }
