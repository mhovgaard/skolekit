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


def test_compacts_source_and_builds_stable_cache_key() -> None:
    source = candidate()
    document = candidate_document(source, class_labels={"Alfa": "2. klasse"})

    assert "body_html" not in document
    assert "change_type" not in document
    assert "recipients" not in document
    assert "previous_messages_text" not in document["source_data"]["message"]
    assert "content_html" not in document["source_data"]["days"][0]["plans"][0]
    assert document["children"] == ["Alfa"]
    assert document["child_context"] == [{"child": "Alfa", "class": "2. klasse"}]
    assert derivation_cache_key(source, model="model", document=document) == (
        derivation_cache_key(source, model="model", document=document)
    )


def test_openrouter_defaults_are_explicit() -> None:
    assert PROVIDER == "openrouter"
    assert DEFAULT_MODEL == "openai/gpt-5.6-terra"
    assert DEFAULT_BASE_URL == "https://openrouter.ai/api/v1"


def test_openrouter_request_enforces_structured_output_and_zdr(monkeypatch) -> None:
    captured: dict = {}

    class FakeResponse:
        is_success = True

        def json(self) -> dict:
            return {
                "id": "response-id",
                "choices": [{"message": {"content": '{"items":[]}'}}],
                "usage": {},
            }

    class FakeClient:
        def __init__(self, *, timeout: float) -> None:
            captured["timeout"] = timeout

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def post(self, url: str, *, headers: dict, json: dict) -> FakeResponse:
            captured.update(url=url, headers=headers, body=json)
            return FakeResponse()

    monkeypatch.setattr("skoleklar.triage.derivation.httpx.Client", FakeClient)

    result = OpenRouterClient(api_key="test-secret").derive({"source": "x"})

    assert result["items"] == []
    assert captured["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer test-secret"
    assert captured["body"]["model"] == "openai/gpt-5.6-terra"
    assert captured["body"]["provider"] == {
        "require_parameters": True,
        "zdr": True,
    }
    assert captured["body"]["response_format"]["type"] == "json_schema"
    assert captured["body"]["response_format"]["json_schema"]["strict"] is True


def test_openrouter_reuses_injected_http_client() -> None:
    class FakeResponse:
        is_success = True

        def json(self) -> dict:
            return {
                "id": "response-id",
                "choices": [{"message": {"content": '{"items":[]}'}}],
                "usage": {},
            }

    class SharedClient:
        def __init__(self) -> None:
            self.calls = 0

        def post(self, *_args, **_kwargs) -> FakeResponse:
            self.calls += 1
            return FakeResponse()

    shared = SharedClient()
    client = OpenRouterClient(api_key="test-secret", client=shared)

    client.derive({"source": "first"})
    client.derive({"source": "second"})

    assert shared.calls == 2


def test_openrouter_http_error_preserves_status_code(monkeypatch) -> None:
    class FakeResponse:
        is_success = False
        status_code = 402
        reason_phrase = "Payment Required"

        def json(self) -> dict:
            return {"error": {"message": "Add credits"}}

    class FakeClient:
        def __init__(self, *, timeout: float) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def post(self, *args, **kwargs) -> FakeResponse:
            return FakeResponse()

    monkeypatch.setattr("skoleklar.triage.derivation.httpx.Client", FakeClient)

    try:
        OpenRouterClient(api_key="test-secret").derive({"source": "x"})
    except OpenRouterHTTPError as error:
        assert error.status_code == 402
        assert "Add credits" in str(error)
    else:
        raise AssertionError("Expected OpenRouterHTTPError")


def test_openrouter_reports_truncated_structured_output_without_content(
    monkeypatch,
) -> None:
    class FakeResponse:
        is_success = True

        def json(self) -> dict:
            return {
                "id": "response-id",
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {"content": '{"items":['},
                    }
                ],
                "usage": {"total_tokens": 2500, "cost": 0.01},
            }

    class FakeClient:
        def __init__(self, *, timeout: float) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def post(self, *args, **kwargs) -> FakeResponse:
            return FakeResponse()

    monkeypatch.setattr("skoleklar.triage.derivation.httpx.Client", FakeClient)

    try:
        OpenRouterClient(api_key="test-secret").derive({"source": "x"})
    except OpenRouterGenerationError as error:
        assert error.stage == "model_output_decode"
        assert error.error_code == "max_output_tokens"
        assert error.response_id == "response-id"
        assert error.usage == {"total_tokens": 2500, "cost": 0.01}
        assert "items" not in str(error)
    else:
        raise AssertionError("Expected OpenRouterGenerationError")


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


def test_accepts_ordered_ellipsis_in_evidence_quote() -> None:
    source = candidate()
    document = candidate_document(source, class_labels={"Alfa": "2. klasse"})
    value = validate_items(
        [item(evidence="Lektie: Læs side 18 ... før mandag.")],
        candidate=source,
        document=document,
    )[0]

    assert value["needs_review"] is False


def test_removes_non_danish_characters_and_marks_item_for_review() -> None:
    source = candidate()
    document = candidate_document(source, class_labels={"Alfa": "2. klasse"})
    contaminated = item()
    contaminated["summary"] = "Læs side 18 inden mandag些"

    value = validate_items(
        [contaminated], candidate=source, document=document
    )[0]

    assert value["summary"] == "Læs side 18 inden mandag"
    assert value["needs_review"] is True
    assert "non-Danish" in value["review_reason"]


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


def test_content_data_that_says_more_is_kept_and_other_sources_keep_their_text() -> None:
    def notice(content_data: dict) -> dict:
        payload = {"source_type": "frontpage", "body_text": "Skolen holder lukket fredag.",
                   "source_data": {"title": "Lukket", "id": 4}, "content_data": content_data}
        return {"source_type": "frontpage", "sources": [{"version_id": 1, "child": "Alfa",
                                                         "payload_json": json.dumps(payload)}]}

    repeated = candidate_document(notice({"title": "Lukket"}), class_labels={})
    assert "content_data" not in repeated and repeated["body_text"] == "Skolen holder lukket fredag."
    different = candidate_document(notice({"title": "Lukket", "note": "Kun 0. klasse"}), class_labels={})
    assert different["content_data"] == {"title": "Lukket", "note": "Kun 0. klasse"}


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
