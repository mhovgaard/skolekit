from datetime import date
from pathlib import Path

from skolekit.records import SourceCheck, SourceRecord
from skoleklar.storage import SnapshotStore, source_date_from_payload


def record(
    *,
    body: str = "Original",
    raw_nonce: int = 1,
    student_id: int = 1,
    child: str = "Child",
    source_type: str = "frontpage",
    source_key: str = "pin:1",
) -> SourceRecord:
    return SourceRecord(
        student_id=student_id,
        child=child,
        source_type=source_type,
        source_key=source_key,
        source_url=f"https://x.skoleintra.dk/parent/{student_id}/{child}/Index#{source_key}",
        title="Notice",
        body_text=body,
        raw_payload={"body": body, "nonce": raw_nonce},
    )


def dated_record(*, key: str, date_text: str) -> SourceRecord:
    """A front-page notice with its display date, as the site shows it."""
    return SourceRecord(
        student_id=1,
        child="Child",
        source_type="frontpage",
        source_key=key,
        source_url=f"https://x.skoleintra.dk/parent/1/Child/Index#{key}",
        title="Notice",
        body_text=f"Useful current notice {key}",
        dates=[{"kind": "display_timestamp", "value": date_text}],
    )


def check(
    records: list[SourceRecord],
    *,
    student_id: int = 1,
    child: str = "Child",
    source_type: str = "frontpage",
) -> SourceCheck:
    return SourceCheck(
        student_id=student_id,
        child=child,
        source_type=source_type,
        records=records,
        complete=True,
    )


def store_run(
    store: SnapshotStore,
    records: list[SourceRecord],
    *,
    source_type: str = "frontpage",
) -> dict:
    run_id = store.begin_run()
    return store.store_checks(run_id, [check(records, source_type=source_type)])


def test_snapshot_deduplicates_and_separates_raw_content_changes(
    tmp_path: Path,
) -> None:
    database = tmp_path / "school.db"
    with SnapshotStore(database) as store:
        first = store_run(store, [record()])
        second = store_run(store, [record(raw_nonce=2)])
        second_diff = store.diff(second["run_id"])
        second_noisy_diff = store.diff(second["run_id"], include_noise=True)
        third = store_run(store, [record(body="Changed", raw_nonce=3)])
        third_diff = store.diff(third["run_id"])

    assert first["changes"] == {"new": 1}
    assert second["changes"] == {"raw_changed": 1}
    assert second_diff["items"] == []
    assert second_noisy_diff["counts"] == {"raw_changed": 1}
    assert third_diff["counts"] == {"content_changed": 1}
    assert database.stat().st_mode & 0o777 == 0o600


def test_harvest_can_be_finalized_after_message_processing(tmp_path: Path) -> None:
    with SnapshotStore(tmp_path / "school.db") as store:
        result = store_run(store, [record()])
        store.finalize_harvest_run(result["run_id"], status="partial")
        row = store.connection.execute(
            "SELECT status, completed_at FROM harvest_runs WHERE id = ?",
            (result["run_id"],),
        ).fetchone()

    assert row["status"] == "partial"
    assert row["completed_at"] is not None


def test_removal_requires_two_complete_missing_harvests(tmp_path: Path) -> None:
    with SnapshotStore(tmp_path / "school.db") as store:
        store_run(store, [record()])
        missing = store_run(store, [])
        removed = store_run(store, [])

        assert store.diff(missing["run_id"])["items"] == []
        assert store.diff(removed["run_id"])["items"] == []
        assert store.diff(missing["run_id"], include_noise=True)["counts"] == {
            "missing": 1
        }
        assert store.diff(removed["run_id"], include_noise=True)["counts"] == {
            "removed": 1
        }


def test_confirmed_non_frontpage_removal_is_meaningful(tmp_path: Path) -> None:
    calendar_record = record(source_type="calendar", source_key="event:1")
    with SnapshotStore(tmp_path / "school.db") as store:
        store_run(store, [calendar_record], source_type="calendar")
        missing = store_run(store, [], source_type="calendar")
        removed = store_run(store, [], source_type="calendar")

        assert store.diff(missing["run_id"])["items"] == []
        assert store.diff(removed["run_id"])["counts"] == {"removed": 1}


def test_reordering_records_is_not_a_meaningful_change(tmp_path: Path) -> None:
    first = record(source_key="pin:1")
    second = record(source_key="pin:2", body="Second")
    with SnapshotStore(tmp_path / "school.db") as store:
        store_run(store, [first, second])
        reordered = store_run(store, [second, first])

        assert store.diff(reordered["run_id"])["items"] == []


def test_identical_frontpage_notice_is_one_household_change(tmp_path: Path) -> None:
    rita = record(student_id=1, child="Beta", source_key="skoleintra-pin:3059")
    august = record(student_id=2, child="Alfa", source_key="skoleintra-pin:3059")
    with SnapshotStore(tmp_path / "school.db") as store:
        run_id = store.begin_run()
        result = store.store_checks(
            run_id,
            [
                SourceCheck(1, "Beta", "frontpage", [rita]),
                SourceCheck(2, "Alfa", "frontpage", [august]),
            ],
        )
        diff = store.diff(result["run_id"])
        noisy_diff = store.diff(result["run_id"], include_noise=True)

    assert diff["counts"] == {"new": 1}
    assert diff["items"][0]["child"] == "household"
    assert diff["items"][0]["children"] == ["Alfa", "Beta"]
    assert len(diff["items"][0]["source_urls"]) == 2
    assert noisy_diff["counts"] == {"new": 2}


def test_derivation_candidates_and_workflow_persist_per_household_item(
    tmp_path: Path,
) -> None:
    rita = record(student_id=1, child="Beta", source_key="school:1")
    august = record(student_id=2, child="Alfa", source_key="school:1")
    database = tmp_path / "school.db"
    with SnapshotStore(database) as store:
        run_id = store.begin_run()
        store.store_checks(
            run_id,
            [
                SourceCheck(1, "Beta", "frontpage", [rita]),
                SourceCheck(2, "Alfa", "frontpage", [august]),
            ],
        )
        candidates = store.derivation_candidates(run_id)
        assert len(candidates) == 1
        assert len(candidates[0]["sources"]) == 2

        derivation_id = store.begin_derivation(
            harvest_run_id=run_id,
            cache_key="cache",
            provider="test",
            model="test-model",
            prompt_version="p1",
            taxonomy_version="t1",
        )
        item_ids = store.complete_derivation(
            derivation_id,
            items=[
                {
                    "stable_key": "household-notice",
                    "stream": "context",
                    "type": "context",
                    "audience": "family",
                    "child": "household",
                    "title": "Notice",
                    "summary": "School notice",
                    "effective_date": None,
                    "end_date": None,
                    "all_day": True,
                    "timing_rule": "no_derived_date",
                    "actionability": "informational",
                    "urgency": "routine",
                    "urgency_reason": "No action",
                    "confidence": 0.99,
                    "needs_review": False,
                    "review_reason": "",
                    "evidence": [{"quote": "Original", "source_day": None, "subject": None}],
                    "dedupe_hint": "notice",
                }
            ],
            sources=candidates[0]["sources"],
            response_id="response-1",
            usage={"total_tokens": 10},
        )
        current = store.current_derived_items()
        report = store.derivation_report(
            prompt_version="p1", model="test-model", source_types=["frontpage"]
        )
        consolidation_id = store.begin_consolidation(
            harvest_run_id=run_id,
            cache_key="consolidation-cache",
            provider="test",
            model="test-model",
            prompt_version="consolidate-v1",
            taxonomy_version="t1",
            manifest_hash="manifest-hash",
            input_count=1,
        )
        store.complete_consolidation(
            consolidation_id,
            items=[
                {
                    **{
                        key: current[0][key]
                        for key in (
                            "stable_key",
                            "stream",
                            "item_type",
                            "audience",
                            "child",
                            "title",
                            "summary",
                            "effective_date",
                            "end_date",
                            "all_day",
                            "timing_rule",
                            "actionability",
                            "urgency",
                            "urgency_reason",
                            "confidence",
                            "needs_review",
                            "review_reason",
                            "source_urls",
                            "source_types",
                            "harvest_run_id",
                        )
                    },
                    "resolution": "unchanged",
                    "member_item_ids": item_ids,
                    "reason": "No duplicate",
                }
            ],
            response_id="consolidation-response",
            usage={"total_tokens": 20},
        )
        consolidation_report = store.consolidation_report(consolidation_id)
        store.record_delivery(item_ids)
        delivered = store.current_derived_items()
        action = store.update_item_workflow(item_ids[0], "acknowledge")
        acknowledged = store.current_derived_items()

    assert current[0]["child"] == "household"
    assert report["documents"][0]["items"][0]["all_day"] is True
    assert consolidation_report is not None
    assert consolidation_report["input_count"] == 1
    assert consolidation_report["output_count"] == 1
    assert consolidation_report["reduction_count"] == 0
    assert consolidation_report["items"][0]["member_item_ids"] == item_ids
    assert len(current[0]["source_urls"]) == 2
    assert current[0]["first_delivered_at"] is None
    assert delivered[0]["first_delivered_at"] is not None
    assert action["action"] == "acknowledge"
    assert acknowledged[0]["acknowledged_at"] is not None


def test_archival_is_audited_excluded_and_reversible(tmp_path: Path) -> None:
    with SnapshotStore(tmp_path / "school.db") as store:
        run = store_run(store, [record()])
        candidate = store.derivation_candidates(run["run_id"])[0]
        derivation_id = store.begin_derivation(
            harvest_run_id=run["run_id"],
            cache_key="archive-test",
            provider="test",
            model="test",
            prompt_version="test",
            taxonomy_version="test",
        )
        item_ids = store.complete_derivation(
            derivation_id,
            items=[
                {
                    "stable_key": "expired-homework",
                    "stream": "homework",
                    "type": "homework",
                    "audience": "child",
                    "child": "Child",
                    "title": "Old homework",
                    "summary": "Finished last week",
                    "effective_date": "2026-09-01",
                    "end_date": "2026-09-01",
                    "all_day": True,
                    "timing_rule": "explicit",
                    "actionability": "required",
                    "urgency": "routine",
                    "urgency_reason": "Past",
                    "confidence": 1.0,
                    "needs_review": False,
                    "review_reason": "",
                    "evidence": [],
                    "dedupe_hint": "old",
                }
            ],
            sources=candidate["sources"],
            response_id="",
            usage={},
        )
        archival_run_id = store.apply_archival(
            as_of=date(2026, 9, 12),
            policy_version="archive-v1",
            decisions=[
                {
                    "item_id": item_ids[0],
                    "stable_key": "expired-homework",
                    "decision": "archive",
                    "reason": "expired_homework",
                    "method": "policy",
                }
            ],
        )
        report = store.archival_report(archival_run_id)

        assert store.current_derived_items() == []
        assert len(store.current_derived_items(include_archived=True)) == 1
        assert report is not None
        assert report["archived_count"] == 1
        assert report["decisions"][0]["reason"] == "expired_homework"

        store.update_item_workflow(item_ids[0], "restore")
        assert len(store.current_derived_items()) == 1


def test_backfill_is_date_bounded(tmp_path: Path) -> None:
    with SnapshotStore(tmp_path / "school.db") as store:
        store_run(store, [
            dated_record(key="pin:3", date_text="Onsdag, 19. aug. 2026 07:43"),
            dated_record(key="pin:2", date_text="Torsdag, 2. jul. 2026 15:51"),
        ])
        candidates = store.backfill_candidates(
            since=date(2026, 8, 1), source_types=["frontpage"], limit=10
        )

    assert [candidate["source_key"] for candidate in candidates] == ["pin:3"]
    assert candidates[0]["source_date"] == "2026-08-19"


def test_zero_item_derivation_is_auditable_and_supersedes_old_items(
    tmp_path: Path,
) -> None:
    database = tmp_path / "school.db"
    with SnapshotStore(database) as store:
        store_run(store, [dated_record(key="pin:3", date_text="Onsdag, 19. aug. 2026 07:43")])
        run_id = store.latest_completed_run_id()
        candidate = store.backfill_candidates(
            since=date(2026, 8, 1), source_types=["frontpage"], limit=10
        )[0]
        old_id = store.begin_derivation(
            harvest_run_id=run_id,
            cache_key="old",
            provider="test",
            model="model",
            prompt_version="p1",
            taxonomy_version="t1",
        )
        store.complete_derivation(
            old_id,
            items=[
                {
                    "stable_key": "old-item",
                    "stream": "context",
                    "type": "context",
                    "audience": "child",
                    "child": "Child",
                    "title": "Old",
                    "summary": "Old summary",
                    "effective_date": None,
                    "end_date": None,
                    "all_day": True,
                    "timing_rule": "no_derived_date",
                    "actionability": "informational",
                    "urgency": "routine",
                    "urgency_reason": "Information",
                    "confidence": 0.9,
                    "needs_review": False,
                    "review_reason": "",
                    "evidence": [
                        {"quote": "Useful", "source_day": None, "subject": None}
                    ],
                    "dedupe_hint": "old",
                }
            ],
            sources=candidate["sources"],
            response_id="old-response",
            usage={},
        )
        new_id = store.begin_derivation(
            harvest_run_id=run_id,
            cache_key="new",
            provider="test",
            model="model",
            prompt_version="p2",
            taxonomy_version="t1",
            purpose="validation",
        )
        store.complete_derivation(
            new_id,
            items=[],
            sources=candidate["sources"],
            response_id="new-response",
            usage={},
        )
        report = store.derivation_report(
            prompt_version="p2",
            model="model",
            source_types=["frontpage"],
            since=date(2026, 8, 1),
        )
        validation_isolated = store.current_derived_items()
        store.activate_derivation(new_id)
        current_after_activation = store.current_derived_items()

    assert report["documents_processed"] == 1
    assert report["zero_item_documents"] == 1
    assert report["documents"][0]["source_key"] == "pin:3"
    assert report["documents"][0]["purpose"] == "validation"
    assert [item["title"] for item in validation_isolated] == ["Old"]
    assert current_after_activation == []


def test_parses_danish_source_date() -> None:
    assert source_date_from_payload(
        {
            "source_data": {
                "message": {
                    "sent_received_date_text": "Onsdag, 19. aug. 2026 07:43"
                }
            }
        }
    ) == date(2026, 8, 19)


def test_incomplete_check_makes_run_partial_without_removals(tmp_path: Path) -> None:
    with SnapshotStore(tmp_path / "school.db") as store:
        store_run(store, [record()])
        run_id = store.begin_run()
        result = store.store_checks(
            run_id,
            [
                SourceCheck(
                    student_id=1,
                    child="Child",
                    source_type="frontpage",
                    complete=False,
                )
            ],
        )

        assert result["status"] == "partial"
        assert store.diff(run_id)["items"] == []


def test_page_bounded_refresh_does_not_age_unseen_history(tmp_path: Path) -> None:
    historical = dated_record(key="pin:1", date_text="Mandag, 1. juni 2026 08:00")
    with SnapshotStore(tmp_path / "school.db") as store:
        store_run(store, [historical])
        run_id = store.begin_run()
        result = store.store_checks(
            run_id,
            [
                SourceCheck(
                    student_id=1,
                    child="Child",
                    source_type="frontpage",
                    records=[],
                    complete=True,
                    coverage={"kind": "page_window", "pages": 1},
                )
            ],
        )

        assert result["status"] == "complete"
        assert result["changes"] == {}
        status = store.connection.execute(
            "SELECT status, missing_count FROM records WHERE source_type='frontpage'"
        ).fetchone()
        assert tuple(status) == ("active", 0)


def test_date_bounded_refresh_only_ages_calendar_records_in_window(
    tmp_path: Path,
) -> None:
    def calendar_record(key: str, day: str) -> SourceRecord:
        return SourceRecord(
            student_id=1,
            child="Beta",
            source_type="calendar",
            source_key=key,
            source_url="https://x.skoleintra.dk/calendar",
            title=key,
            dates=[
                {
                    "kind": "event",
                    "start": f"{day}T09:00:00",
                    "end": f"{day}T09:45:00",
                }
            ],
        )

    in_window = calendar_record("event:recent", "2026-08-25")
    old = calendar_record("event:old", "2026-06-01")
    with SnapshotStore(tmp_path / "school.db") as store:
        store_run(store, [in_window, old], source_type="calendar")
        run_id = store.begin_run()
        result = store.store_checks(
            run_id,
            [
                SourceCheck(
                    student_id=1,
                    child="Child",
                    source_type="calendar",
                    records=[],
                    coverage={
                        "kind": "date_window",
                        "start": "2026-08-24",
                        "end": "2026-08-31",
                    },
                )
            ],
        )

        assert result["changes"] == {"missing": 1}
        statuses = {
            row["source_key"]: (row["status"], row["missing_count"])
            for row in store.connection.execute(
                "SELECT source_key, status, missing_count FROM records"
            )
        }
        assert statuses["event:recent"] == ("missing", 1)
        assert statuses["event:old"] == ("active", 0)


def test_reopening_store_migrates_active_lesson_identity(tmp_path: Path) -> None:
    database = tmp_path / "school.db"
    raw = {
        "id": 100,
        "originalId": 42,
        "start": "2026-08-25T08:00:00",
        "end": "2026-08-25T08:45:00",
        "lessonClassName": "001",
        "lessonName": "Math",
        "staffName": "Teacher",
    }
    lesson = SourceRecord(
        student_id=1,
        child="Child",
        source_type="calendar",
        source_key="LessonsEvents:42",
        source_url="https://x.skoleintra.dk/calendar",
        title="Math",
        dates=[{"kind": "event", "start": raw["start"], "end": raw["end"]}],
        content_data={"lessonName": "Math", "staffName": "Teacher"},
        state_data={"calendar_kind": "LessonsEvents"},
        raw_payload=raw,
    )
    with SnapshotStore(database) as store:
        store_run(store, [lesson], source_type="calendar")

    with SnapshotStore(database) as store:
        source_key = store.connection.execute(
            "SELECT source_key FROM records WHERE source_type = 'calendar'"
        ).fetchone()["source_key"]

    assert source_key.startswith("LessonsEvents:slot:")
