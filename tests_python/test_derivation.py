import pytest
import json

from skoleklar.triage.derivation import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    PROVIDER,
    OpenRouterGenerationError,
    OpenRouterHTTPError,
    OpenRouterClient,
    candidate_document,
    deterministic_calendar_items,
    derivation_cache_key,
    validate_items,
)

# This refactor's core set: `pytest -m pipeline` (docs/maintenance.md).
pytestmark = pytest.mark.pipeline


def candidate() -> dict:
    payload = {
        "child": "Alfa",
        "source_type": "weekly_plan",
        "source_key": "class:35-2026",
        "body_html": "<p>ignored</p>",
        "body_text": "Lektie: Læs side 18 før mandag.",
        "recipients": [{"Name": "Private recipient"}],
        "source_data": {
            "message": {"previous_messages_text": "Quoted old message"},
            "days": [
                {
                    "weekday": "Mandag",
                    "date": "2026-08-24",
                    "plans": [
                        {
                            "subject": "Dansk",
                            "content_html": "<p>ignored</p>",
                            "content_text": "Lektie: Læs side 18 før mandag.",
                        }
                    ],
                }
            ]
        },
    }
    return {
        "harvest_run_id": 8,
        "change": "new",
        "source_type": "weekly_plan",
        "source_key": "class:35-2026",
        "content_hash": "abc",
        "sources": [
            {
                "record_id": 1,
                "version_id": 2,
                "child": "Alfa",
                "source_url": "https://x.skoleintra.dk/plan",
                "payload_json": json.dumps(payload),
            }
        ],
    }


def item(*, evidence: str = "Læs side 18 før mandag.") -> dict:
    return {
        "dedupe_hint": "dansk-read-18",
        "stream": "homework",
        "type": "homework",
        "audience": "child",
        "child": "Alfa",
        "title": "Read page 18",
        "summary": "Read page 18 before Monday",
        "effective_date": "2026-08-24",
        "end_date": None,
        "all_day": True,
        "timing_rule": "complete_before_source_day",
        "actionability": "required",
        "urgency": "attention",
        "urgency_reason": "Due Monday",
        "confidence": 0.98,
        "needs_review": False,
        "review_reason": "",
        "evidence": [
            {"quote": evidence, "source_day": "2026-08-24", "subject": "Dansk"}
        ],
    }


def test_ordinary_calendar_lessons_bypass_llm_but_changes_do_not() -> None:
    def calendar_candidate(event: dict) -> dict:
        return {
            "source_type": "calendar",
            "source_key": "LessonsEvents:slot:test",
            "sources": [
                {
                    "version_id": 2,
                    "child": "Alfa",
                    "payload_json": json.dumps(
                        {
                            "source_data": {
                                "calendar_kind": "LessonsEvents",
                                "event": event,
                            }
                        }
                    ),
                }
            ],
        }

    routine = calendar_candidate(
        {
            "title": "DAN",
            "description": "",
            "staffName": "Teacher",
            "className": "",
            "absentStaff": "",
            "substituteStaff": "",
            "substituteExplainedAbbreviations": [],
        }
    )
    substitution = calendar_candidate(
        {
            "title": "STØTTE",
            "lessonName": "STØTTE",
            "start": "2026-09-17T12:15:00",
            "end": "2026-09-17T13:00:00",
            "className": "absent",
            "absentStaff": "Vikar for Teacher",
        }
    )

    assert deterministic_calendar_items(routine) == []
    items = deterministic_calendar_items(substitution)
    assert items is not None
    assert items[0]["type"] == "schedule_change"
    assert items[0]["effective_date"] == "2026-09-17"
    assert items[0]["needs_review"] is False

    named_substitution = calendar_candidate(
        {
            "title": "MAT",
            "lessonName": "MAT",
            "lessonClassName": "2.A",
            "start": "2026-09-18T09:00:00",
            "end": "2026-09-18T09:45:00",
            "staffName": "Original Teacher",
            "substituteStaff": "Substitute Teacher",
        }
    )
    named_items = deterministic_calendar_items(named_substitution)
    assert named_items is not None
    assert named_items[0]["type"] == "substitute"
    assert named_items[0]["title"] == "Substitute Teacher er vikar i MAT · 2.A"
    assert "Substitute Teacher som vikar i MAT · 2.A" in named_items[0]["summary"]

    regular_teacher_mislabeled_as_substitute = calendar_candidate(
        {
            "title": "MAT",
            "lessonName": "MAT",
            "lessonClassName": "02",
            "start": "2026-09-18T12:15:00",
            "end": "2026-09-18T13:00:00",
            "staffName": "Linette Kirsten Lund",
            "substituteStaff": "Linette Kirsten Lund",
            "absentStaff": "(Vikar for Linette Kirsten Lund)",
            "substitutionExtraTitle": "Vikar",
        }
    )
    assert deterministic_calendar_items(regular_teacher_mislabeled_as_substitute) == []


def test_validates_evidence_and_adds_internal_stable_key() -> None:
    source = candidate()
    document = candidate_document(source, class_labels={"Alfa": "2. klasse"})

    valid = validate_items([item()], candidate=source, document=document)[0]
    invalid = validate_items(
        [item(evidence="This text is not present")],
        candidate=source,
        document=document,
    )[0]

    assert valid["needs_review"] is False
    assert len(valid["stable_key"]) == 64
    assert invalid["needs_review"] is True
    assert "Evidence" in invalid["review_reason"]


def test_a_weekly_plan_goes_to_the_model_once() -> None:
    """derive-v10: one copy of the plan, without code's heading guesses, links or ids."""
    source = candidate()
    payload = json.loads(source["sources"][0]["payload_json"])
    block = payload["source_data"]["days"][0]["plans"][0]
    block.update(segments=[{"section_hint": "homework", "text": "Læs side 18 før mandag."}],
                 link="https://x.skoleintra.dk/plan/7", source_plan_id=7)
    payload["source_data"]["general_plans"] = [{"subject": None, "content_text": "Kære forældre", "segments": []}]
    payload["content_data"] = {"days": payload["source_data"]["days"],
                               "general_plans": payload["source_data"]["general_plans"]}
    source["sources"][0]["payload_json"] = json.dumps(payload)

    document = candidate_document(source, class_labels={})

    assert "body_text" not in document and "content_data" not in document
    block = document["source_data"]["days"][0]["plans"][0]
    assert block == {"subject": "Dansk", "content_text": "Lektie: Læs side 18 før mandag."}
    assert document["source_data"]["general_plans"] == [{"subject": None, "content_text": "Kære forældre"}]
    # A quote from a plan block still passes the evidence check.
    [checked] = validate_items([item()], candidate=source, document=document)
    assert checked["needs_review"] is False


def test_what_an_item_is_is_kept_only_where_it_means_something() -> None:
    source = candidate()
    document = candidate_document(source, class_labels={})
    homework = item() | {"category": "trip", "moves_time": True, "recurring": True}
    event = item() | {"type": "event", "category": "party", "moves_time": True, "recurring": True}
    unknown = item() | {"type": "event", "category": "picnic", "moves_time": None, "recurring": False}
    action = item() | {"type": "parent_action", "category": "trip", "moves_time": None, "recurring": False}
    meeting = item() | {"type": "parent_action", "category": "meeting", "moves_time": None, "recurring": True}
    change = item() | {"type": "schedule_change", "category": "other", "moves_time": True, "recurring": True}
    undecided = item() | {"type": "schedule_change", "category": None, "moves_time": None, "recurring": False}
    result = validate_items([homework, event, unknown, action, meeting, change, undecided],
                            candidate=source, document=document)
    shape = [(i["category"], i["moves_time"], i["recurring"]) for i in result]
    assert shape == [(None, None, False), ("party", None, True), (None, None, False), (None, None, False),
                     ("meeting", None, False), (None, True, False), (None, None, False)]
    # A wrong field is dropped, never sent for review: it would hide real homework.
    assert not any(i["needs_review"] for i in result)


def test_lesson_slots_say_what_they_are() -> None:
    candidate_ = {"source_type": "calendar", "source_key": "LessonsEvents:slot:x", "sources": [{
        "version_id": 2, "child": "Alfa", "payload_json": json.dumps({"source_data": {
            "calendar_kind": "LessonsEvents", "event": {
                "title": "MAT", "lessonName": "MAT", "start": "2026-09-18T09:00:00",
                "end": "2026-09-18T09:45:00", "staffName": "Original Teacher",
                "substituteStaff": "Substitute Teacher"}}})}]}
    [lesson] = deterministic_calendar_items(candidate_)
    assert (lesson["type"], lesson["category"], lesson["moves_time"], lesson["recurring"]) == (
        "substitute", None, None, False)


def test_a_lesson_changes_identity_survives_the_readers_new_version() -> None:
    """The reader's version moved with the fact contract; a lesson change's stable key
    did not, so a substitute the family ticked off stays ticked off."""
    from skolekit.records import canonical_hash

    candidate_ = {"source_type": "calendar", "source_key": "LessonsEvents:slot:x", "sources": [{
        "version_id": 2, "child": "Alfa", "payload_json": json.dumps({"source_data": {
            "calendar_kind": "LessonsEvents", "event": {
                "title": "MAT", "lessonName": "MAT", "start": "2026-09-18T09:00:00",
                "end": "2026-09-18T09:45:00", "staffName": "Original Teacher",
                "substituteStaff": "Substitute Teacher"}}})}]}
    [lesson] = deterministic_calendar_items(candidate_)
    assert lesson["stable_key"] == canonical_hash({"filter_version": "routine-calendar-v3",
                                                   "source_key": "LessonsEvents:slot:x", "child": "Alfa",
                                                   "type": "substitute"})
    # It says what it is in typed fields, and carries the current contract.
    assert (lesson["teacher"], lesson["school_subject"], lesson["contract"]) == (
        "Substitute Teacher", "MAT", "fact-v1")
