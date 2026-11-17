from datetime import date

from skoleklar.brief import (
    brief_item_stable_keys,
    build_brief,
    build_brief_range,
    render_brief_range_text,
    render_brief_text,
)


def derived_item(**overrides: object) -> dict:
    value = {
        "id": 12,
        "stable_key": "key",
        "item_type": "homework",
        "stream": "homework",
        "audience": "child",
        "child": "Beta",
        "title": "Read",
        "summary": "Read pages 18–19",
        "effective_date": "2026-08-24",
        "end_date": None,
        "all_day": True,
        "timing_rule": "complete_before_source_day",
        "actionability": "required",
        "urgency": "routine",
        "urgency_reason": "Due today",
        "confidence": 0.98,
        "needs_review": False,
        "review_reason": "",
        "harvest_run_id": 8,
        "first_delivered_at": None,
        "acknowledged_at": None,
        "completed_at": None,
        "dismissed_at": None,
    }
    value.update(overrides)
    return value


def test_unseen_urgent_item_is_red_then_seen_is_amber() -> None:
    target = date(2026, 8, 24)
    unseen = build_brief(
        [derived_item(urgency="urgent")], target_date=target, latest_run_id=8
    )
    seen = build_brief(
        [
            derived_item(
                urgency="urgent", first_delivered_at="2026-08-23T18:00:00Z"
            )
        ],
        target_date=target,
        latest_run_id=8,
    )

    assert unseen["signal"]["level"] == "red"
    assert seen["signal"]["level"] == "amber"
    assert "Beta" in render_brief_text(unseen)


def test_routine_homework_is_amber_not_red() -> None:
    brief = build_brief(
        [derived_item()], target_date=date(2026, 8, 24), latest_run_id=8
    )

    assert brief["signal"]["level"] == "amber"


def test_due_required_parent_action_escalates_to_red_until_seen() -> None:
    item = derived_item(
        item_type="parent_action",
        stream="actions",
        audience="parent",
        actionability="required",
        urgency="attention",
    )
    unseen = build_brief(
        [item], target_date=date(2026, 8, 24), latest_run_id=8
    )
    seen = build_brief(
        [{**item, "first_delivered_at": "2026-08-24T06:00:00Z"}],
        target_date=date(2026, 8, 24),
        latest_run_id=8,
    )

    assert unseen["signal"]["level"] == "red"
    assert seen["signal"]["level"] == "amber"


def test_ambiguous_due_action_requires_review_without_red_alarm() -> None:
    brief = build_brief(
        [
            derived_item(
                item_type="parent_action",
                stream="actions",
                audience="parent",
                actionability="required",
                urgency="attention",
                needs_review=True,
            )
        ],
        target_date=date(2026, 8, 24),
        latest_run_id=8,
    )

    assert brief["signal"]["level"] == "amber"
    assert brief["signal"]["review_count"] == 1


def test_overdue_parent_action_remains_in_brief() -> None:
    brief = build_brief(
        [
            derived_item(
                item_type="parent_action",
                stream="actions",
                audience="parent",
                effective_date="2026-08-23",
                harvest_run_id=7,
            )
        ],
        target_date=date(2026, 8, 24),
        latest_run_id=8,
    )

    assert [item["type"] for item in brief["items"]] == ["parent_action"]


def test_past_event_from_latest_change_does_not_create_false_alarm() -> None:
    brief = build_brief(
        [
            derived_item(
                item_type="event",
                stream="calendar",
                effective_date="2026-08-20",
                end_date="2026-08-20",
                urgency="attention",
            )
        ],
        target_date=date(2026, 8, 22),
        latest_run_id=8,
    )

    assert brief["signal"]["level"] == "green"
    assert brief["items"] == []


def test_future_event_from_latest_change_waits_until_its_date() -> None:
    brief = build_brief(
        [
            derived_item(
                item_type="event",
                stream="calendar",
                effective_date="2026-08-26",
                end_date="2026-08-26",
                urgency="attention",
            )
        ],
        target_date=date(2026, 8, 24),
        latest_run_id=8,
    )

    assert brief["signal"]["level"] == "green"
    assert brief["items"] == []


def test_expired_required_reminder_does_not_keep_brief_red() -> None:
    brief = build_brief(
        [
            derived_item(
                item_type="reminder",
                stream="actions",
                effective_date="2026-08-20",
                end_date="2026-08-20",
                actionability="required",
                urgency="attention",
            )
        ],
        target_date=date(2026, 8, 24),
        latest_run_id=8,
    )

    assert brief["signal"]["level"] == "green"
    assert brief["items"] == []


def test_compact_range_is_strictly_dated_and_hides_routine_lessons() -> None:
    items = [
        derived_item(summary="Monday homework"),
        derived_item(
            id=13,
            stable_key="event",
            item_type="event",
            stream="calendar",
            summary="Two-day event",
            effective_date="2026-08-23",
            end_date="2026-08-24",
            actionability="informational",
        ),
        derived_item(
            id=14,
            stable_key="lesson",
            item_type="lesson_preview",
            stream="context",
            summary="Routine lesson",
            actionability="informational",
        ),
        derived_item(
            id=15,
            stable_key="undated",
            item_type="context",
            stream="context",
            summary="Undated context",
            effective_date=None,
            actionability="informational",
        ),
    ]

    brief = build_brief_range(
        items,
        start_date=date(2026, 8, 24),
        end_date=date(2026, 8, 30),
        compact=True,
    )

    assert [item["summary"] for item in brief["items"]] == [
        "Two-day event",
        "Monday homework",
    ]
    assert "Routine lesson" not in render_brief_range_text(brief)
    assert len(brief["days"]) == 7


def test_compact_range_adaptively_groups_new_schedule_topics() -> None:
    items = [
        derived_item(
            id=21,
            stable_key="robot-a",
            item_type="schedule_change",
            stream="calendar",
            title="Skemaændring i ROBOTVÆRKSTED",
            summary=(
                "Beta har en skemaændring i ROBOTVÆRKSTED den 2026-08-24 "
                "kl. 09:00-09:45."
            ),
            source_urls=["https://x.skoleintra.dk/calendar/a"],
            # The timetable's change says its lesson and times; the brief reads those.
            school_subject="ROBOTVÆRKSTED", start_time="09:00", end_time="09:45",
        ),
        derived_item(
            id=22,
            stable_key="robot-b",
            item_type="schedule_change",
            stream="calendar",
            title="  skemaændring   i robotværksted ",
            summary=(
                "Beta har en skemaændring i ROBOTVÆRKSTED den 2026-08-24 "
                "kl. 09:00-09:45."
            ),
            source_urls=["https://x.skoleintra.dk/calendar/b"],
            school_subject="ROBOTVÆRKSTED", start_time="09:00", end_time="09:45",
        ),
        derived_item(
            id=23,
            stable_key="robot-c",
            item_type="schedule_change",
            stream="calendar",
            title="Skemaændring i ROBOTVÆRKSTED",
            summary=(
                "Beta har en skemaændring i ROBOTVÆRKSTED den 2026-08-24 "
                "kl. 10:00-10:45."
            ),
            source_urls=["https://x.skoleintra.dk/calendar/c"],
            school_subject="ROBOTVÆRKSTED", start_time="10:00", end_time="10:45",
        ),
    ]

    brief = build_brief_range(
        items,
        start_date=date(2026, 8, 24),
        end_date=date(2026, 8, 24),
        compact=True,
    )

    assert brief["atomic_item_count"] == 3
    assert brief["item_count"] == 1
    assert brief["compaction"] == {
        "applied": True,
        "display_item_count": 1,
        "atomic_item_count": 3,
        "collapsed_item_count": 2,
        "schedule_group_count": 1,
    }
    grouped = brief["items"][0]
    assert grouped["group_kind"] == "adaptive_schedule"
    assert grouped["member_count"] == 3
    assert grouped["detail_count"] == 2
    assert grouped["duplicate_count"] == 1
    assert "09:00–09:45, 10:00–10:45" in grouped["summary"]
    assert len(grouped["source_urls"]) == 3
    assert brief_item_stable_keys(brief["items"]) == [
        "robot-a",
        "robot-b",
        "robot-c",
    ]


def test_compact_range_keeps_private_message_schedule_facts_separate() -> None:
    items = [
        derived_item(
            id=31,
            stable_key="message-event:" + "a" * 64,
            item_type="schedule_change",
            stream="calendar",
            title="Schedule changed",
            summary="Open the private source message if details are needed.",
        ),
        derived_item(
            id=32,
            stable_key="message-event:" + "b" * 64,
            item_type="schedule_change",
            stream="calendar",
            title="Schedule changed",
            summary="Open the private source message if details are needed.",
        ),
    ]

    brief = build_brief_range(
        items,
        start_date=date(2026, 8, 24),
        end_date=date(2026, 8, 24),
        compact=True,
    )

    assert brief["item_count"] == 2
    assert brief["compaction"]["schedule_group_count"] == 0
    assert all(not item.get("grouped") for item in brief["items"])


def test_completed_item_produces_green_light() -> None:
    brief = build_brief(
        [derived_item(completed_at="2026-08-23T19:00:00Z")],
        target_date=date(2026, 8, 24),
        latest_run_id=8,
    )

    assert brief["signal"]["level"] == "green"
    assert brief["signal"]["reason"] == "no urgent changes"
    assert brief["signal"]["item_ids"] == []
    assert brief["items"] == []
