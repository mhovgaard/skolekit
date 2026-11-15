from datetime import datetime
from zoneinfo import ZoneInfo

from tests_python.synthetic_household import SCHOOL as HOUSEHOLD_SCHOOL
from skolekit.profile import Child
from skoleklar.harvest import HarvestPolicy, scope_for
from skolekit.sources import Scope, recent_calendar_bounds
from skolekit.sources.calendar import _calendar_record


def test_lesson_identity_uses_stable_slot_not_volatile_api_ids() -> None:
    child = Child(student_id=1, child="Child", calendar_class_name="001", school=HOUSEHOLD_SCHOOL)
    first = _calendar_record(
        child,
        "LessonsEvents",
        "https://x.skoleintra.dk/calendar",
        {
            "id": "generated-a",
            "originalId": 42,
            "title": "Math",
            "start": "2026-08-20T08:00:00",
            "end": "2026-08-20T08:45:00",
            "lessonClassName": "001",
            "lessonName": "Math",
            "staffName": "Teacher",
            "substituteStaff": None,
        },
    )
    second = _calendar_record(
        child,
        "LessonsEvents",
        "https://x.skoleintra.dk/calendar",
        {
            "id": "generated-b",
            "originalId": 99,
            "title": "Math",
            "start": "2026-08-20T08:00:00",
            "end": "2026-08-20T08:45:00",
            "lessonClassName": "001",
            "lessonName": "Math",
            "staffName": "Teacher",
            "substituteStaff": "Substitute",
        },
    )

    assert first.source_key == second.source_key
    assert first.source_key.startswith("LessonsEvents:slot:")
    assert first.hashes()["content_hash"] != second.hashes()["content_hash"]
    assert first.hashes()["raw_hash"] != second.hashes()["raw_hash"]


def test_harvest_policy_presets_are_selective_by_default() -> None:
    refresh = HarvestPolicy.for_mode("refresh")
    daily = HarvestPolicy.for_mode("daily")
    latest = HarvestPolicy.for_mode("latest")
    initialize = HarvestPolicy.for_mode("initialize")

    assert (refresh.message_pages, refresh.weekly_plan_weeks) == (1, 2)
    assert (
        daily.calendar_past_weeks,
        daily.calendar_future_weeks,
        daily.weekly_plan_weeks,
        daily.message_pages,
        daily.frontpage_pages,
    ) == (1, 2, 2, 1, 1)
    assert (latest.calendar_past_weeks, latest.calendar_future_weeks) == (0, 0)
    assert initialize.message_pages is None
    assert initialize.weekly_plan_weeks is None


def test_recent_calendar_bounds_align_to_copenhagen_school_weeks() -> None:
    policy = HarvestPolicy.for_mode(
        "refresh", calendar_past_weeks=1, calendar_future_weeks=2
    )
    start, end = recent_calendar_bounds(
        scope_for(policy),
        datetime(2026, 8, 25, 12, tzinfo=ZoneInfo("Europe/Copenhagen")),
    )

    assert start.isoformat() == "2026-08-17T00:00:00+02:00"
    assert end.isoformat() == "2026-09-14T00:00:00+02:00"


def test_a_scope_defaults_to_the_current_view() -> None:
    scope = Scope()
    assert (scope.pages, scope.weeks_back, scope.weeks_ahead, scope.school_year, scope.plan_weeks) == (
        1, 0, 0, False, 1)
    start, end = recent_calendar_bounds(scope, datetime(2026, 11, 12, 7, tzinfo=ZoneInfo("Europe/Copenhagen")))
    assert (start.date().isoformat(), end.date().isoformat()) == ("2026-11-09", "2026-11-16")


def test_a_harvest_policy_is_the_sources_scope() -> None:
    assert scope_for(HarvestPolicy.for_mode("daily")) == Scope(
        pages=1, weeks_back=1, weeks_ahead=2, school_year=False, plan_weeks=2)
    initialize = scope_for(HarvestPolicy.for_mode("initialize"))
    assert initialize.school_year and initialize.pages is None and initialize.plan_weeks is None
