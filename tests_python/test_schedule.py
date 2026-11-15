from skoleklar.schedule import group_substitutions, normalize_lesson_event


def test_normalizes_and_groups_repeated_substitute_lessons() -> None:
    source_url = "https://x.skoleintra.dk/parent/9101/A/calendar/events"
    raw_events = [
        {
            "id": "lesson-1",
            "originalId": 1,
            "start": "2026-08-19T09:00:00",
            "end": "2026-08-19T09:45:00",
            "lessonName": "MAT",
            "staffName": "Original Teacher",
            "absentStaff": "(Vikar for Original Teacher)",
            "substituteStaff": "Substitute Teacher",
            "substitutionExtraTitle": "Vikar",
        },
        {
            "id": "lesson-2",
            "originalId": 2,
            "start": "2026-08-19T10:00:00",
            "end": "2026-08-19T10:45:00",
            "lessonName": "DAN",
            "staffName": "Original Teacher",
            "absentStaff": "(Vikar for Original Teacher)",
            "substituteStaff": "Substitute Teacher",
            "substitutionExtraTitle": "Vikar",
        },
    ]
    normalized = [
        normalize_lesson_event(
            event,
            child="A",
            student_id=9101,
            source_url=source_url,
        )
        for event in raw_events
    ]

    grouped = group_substitutions(normalized)

    assert len(grouped) == 1
    assert grouped[0]["change_kind"] == "substitute"
    assert grouped[0]["subjects"] == ["MAT", "DAN"]
    assert grouped[0]["lesson_count"] == 2
    assert grouped[0]["source_event_ids"] == ["lesson-1", "lesson-2"]
