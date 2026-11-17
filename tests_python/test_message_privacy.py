import html
import json
import pickle
import sqlite3
import threading
import time
from datetime import date
from pathlib import Path

import pytest

from skolekit import sources
from skoleklar.cli import build_parser
from skoleklar.pipeline import snapshot_db_path
from tests_python.synthetic_household import SCHOOL as HOUSEHOLD_SCHOOL
from skolekit.profile import Child
from skoleklar.harvest import HarvestPolicy, collect_all
from skoleklar.delivery_manifest import (
    build_delivery_manifest,
    delivery_items_from_snapshot,
)
from skoleklar.triage.derivation import OpenRouterGenerationError
from skoleklar.triage.message_events import (
    MessageEventExtractionError,
    event_document,
    process_changed_threads,
    project_event_facts,
)
from skolekit.privacy import normalize_latest_activity, shorten_name, shorten_participants
from skoleklar.restricted_messages import (
    MESSAGE_POLICY_VERSION,
    EphemeralThread,
    collect_restricted_messages,
    live_message_output,
    message_handle,
    opaque_handle,
    resolve_message_mode,
    safe_thread_state,
)
from skolekit.records import SourceCheck, SourceRecord
from skoleklar.storage import SnapshotStore


KEY = b"synthetic-test-key"


def _conversation() -> dict:
    return {
        "thread_id": 41,
        "latest_message_id": 73,
        "latest_inbox_message_id": 73,
        "date_text": "13. sep. 2026 08:15",
        "participants_text": "John Brown, Anne Jensen",
        "is_unread": True,
    }


def _message() -> dict:
    return {
        "message_id": 73,
        "subject": "Synthetic private appointment",
        "sender": "John Brown",
        "sent_received_date_text": "13. sep. 2026",
        "body_text": "CANARY_PRIVATE_BODY Meeting at library on 18 September.",
        "is_unread": True,
        "is_outbox": False,
        "is_copied_to_archive": False,
        "attachments": [],
    }


def _inbox_html() -> str:
    payload = {
        "Conversations": [
            {
                "ThreadId": 41,
                "LatestMessageId": 73,
                "LatestInboxMessageId": 73,
                "ThreadParticipantsString": "John Brown",
                "Subject": "CANARY_PRIVATE_SUBJECT",
                "IsUnread": True,
            }
        ],
        "DataProviderSettings": {
            "ExpandThreadMessagesUrl": "/parent/messages/expand",
            "GetMessageForThreadlessConversationUrl": "/parent/messages/single",
            "GetNewConversationsUrl": "/parent/messages/page",
            "LoadMessagesForSelectedConversationUrl": "/parent/messages/load",
            "SearchRequest": "",
        },
    }
    encoded = html.escape(json.dumps(payload), quote=True)
    return (
        '<div data-clientlogic-settings-messageconversationswitharchivefunctionality="'
        f'{encoded}"></div>'
    )


def test_message_mode_defaults_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AG_SCHOOL_MESSAGE_MODE", raising=False)

    assert resolve_message_mode() == "off"
    assert snapshot_db_path().name == "school-restricted.db"


def test_the_legacy_mode_is_removed(monkeypatch: pytest.MonkeyPatch) -> None:
    # It stored message text as records; message text is never stored now.
    with pytest.raises(ValueError, match="legacy message mode was removed"):
        resolve_message_mode("legacy")
    monkeypatch.setenv("AG_SCHOOL_MESSAGE_MODE", "legacy")
    with pytest.raises(ValueError, match="legacy message mode was removed"):
        resolve_message_mode()
    with pytest.raises(ValueError, match="Unsupported message mode"):
        HarvestPolicy.for_mode("initialize", message_mode="legacy")
    with pytest.raises(SystemExit):
        build_parser().parse_args(["harvest", "--message-mode", "legacy"])


def test_shortens_names_without_disclosing_more_on_collisions() -> None:
    assert shorten_name("John Brown") == "John B."
    assert shorten_name("Anne Marie Jensen") == "Anne J."
    assert shorten_name("Åse Østergaard") == "Åse Ø."
    assert shorten_name("Madonna") == "Madonna"
    assert shorten_name("John Brown (teacher)") == "John B."
    assert shorten_participants("John Brown, Jane Brown") == ["John B.", "Jane B."]


def test_opaque_handles_are_stable_and_do_not_contain_source_ids() -> None:
    first = opaque_handle(KEY, namespace="thread", student_id=1, source_id=123456)
    second = opaque_handle(KEY, namespace="thread", student_id=1, source_id=123456)

    assert first == second
    assert first.startswith("thr_")
    assert "123456" not in first


def test_safe_thread_state_contains_only_minimized_current_metadata() -> None:
    result = safe_thread_state(
        _conversation(), student_id=1, child="Child", key=KEY
    )

    assert result["participants"] == ["John B.", "Anne J."]
    assert result["server_unread"] is True
    assert result["is_new_thread"] is True
    assert result["has_new_message"] is False
    assert result["latest_unretrieved"] is True
    assert result["latest_inbox_message_ref"] == result["latest_message_ref"]
    assert result["latest_activity_at"] == "2026-09-13T08:15"
    assert "subject" not in result
    assert "message_count" not in result
    assert "thread_id" not in result
    assert "John Brown" not in json.dumps(result)


def test_normalizes_current_message_chronology_without_inventing_time() -> None:
    assert normalize_latest_activity(
        "19. aug.", observed_on=date(2026, 9, 13)
    ) == "2026-08-19"
    assert normalize_latest_activity(
        "I går 12:49", observed_on=date(2026, 9, 13)
    ) == "2026-09-12T12:49"
    assert normalize_latest_activity(
        "31. dec.", observed_on=date(2026, 1, 2)
    ) == "2025-12-31"


def test_message_event_relative_dates_anchor_to_posting_date() -> None:
    thread = EphemeralThread(
        student_id=1,
        child="Child",
        thread_ref="thr_aaaaaaaaaaaaaaaa",
        latest_message_ref="msg_bbbbbbbbbbbbbbbb",
        conversation=_conversation(),
        messages=[],
    )
    message = {**_message(), "sent_received_date_text": "24. sep. 2026"}

    document = event_document(thread, message, observed_on=date(2026, 9, 26))

    assert document["message_date"] == "2026-09-24"
    assert document["observed_date"] == "2026-09-26"


def test_live_output_exposes_body_only_at_explicit_return_boundary() -> None:
    output = live_message_output(
        _message(), student_id=1, key=KEY, thread_ref="thr_example"
    )

    assert output["sender"] == "John B."
    assert output["body"].startswith("CANARY_PRIVATE_BODY")
    assert output["retention"] == "not_stored_by_ag_school"
    assert "previous_messages_text" not in output


def test_the_harvests_messages_source_never_reads_the_inbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The client is a bare object: any request would fail the check.
    monkeypatch.setattr(sources, "SOURCES", ())
    child = Child(student_id=1, child="Child", calendar_class_name="001", school=HOUSEHOLD_SCHOOL)

    checks = collect_all(
        object(),
        children=(child,),
        policy=HarvestPolicy.for_mode("refresh", message_mode="off"),
    )

    assert len(checks) == 1
    assert checks[0].complete is True
    assert checks[0].metadata["excluded_by_policy"] is True
    assert checks[0].records == []


def test_restricted_collection_does_not_fetch_or_return_bodies_when_disabled() -> None:
    class Response:
        status_code = 200
        url = "https://x.skoleintra.dk/parent/1/Child/messages/conversations"
        text = _inbox_html()

        def raise_for_status(self) -> None:
            return None

    class Client:
        calls = 0

        def get(self, *_args, **_kwargs):
            self.calls += 1
            if self.calls > 1:
                raise AssertionError("message body endpoint must not be called")
            return Response()

    client = Client()
    child = Child(student_id=1, child="Child", calendar_class_name="001", school=HOUSEHOLD_SCHOOL)

    result = collect_restricted_messages(
        client,
        child,
        key=KEY,
        previous_states={},
        page_limit=1,
        page_safety_limit=10,
        fetch_changed_content=False,
    )

    encoded = json.dumps(
        {"metadata": result.check.metadata, "states": result.states},
        ensure_ascii=False,
    )
    assert client.calls == 1
    assert result.changed_threads == []
    assert "CANARY_PRIVATE_SUBJECT" not in encoded
    assert "John Brown" not in encoded


def test_restricted_collection_skips_processed_inbox_message_after_outgoing_reply() -> None:
    previous_conversation = {
        **_conversation(),
        "latest_message_id": 72,
        "latest_inbox_message_id": 71,
    }
    previous_state = safe_thread_state(
        previous_conversation, student_id=1, child="Child", key=KEY
    )
    previous_state["latest_message_processed"] = True
    current_payload = {
        "Conversations": [
            {
                "ThreadId": 41,
                "LatestMessageId": 73,
                "LatestInboxMessageId": 71,
                "ThreadParticipantsString": "John Brown",
                "Subject": "CANARY_PRIVATE_SUBJECT",
                "IsUnread": False,
            }
        ],
        "DataProviderSettings": {
            "ExpandThreadMessagesUrl": "/parent/messages/expand",
            "GetMessageForThreadlessConversationUrl": "/parent/messages/single",
            "GetNewConversationsUrl": "/parent/messages/page",
            "LoadMessagesForSelectedConversationUrl": "/parent/messages/load",
            "SearchRequest": "",
        },
    }

    class Response:
        status_code = 200
        url = "https://x.skoleintra.dk/parent/1/Child/messages/conversations"
        text = (
            '<div data-clientlogic-settings-messageconversationswitharchivefunctionality="'
            f'{html.escape(json.dumps(current_payload), quote=True)}"></div>'
        )

        def raise_for_status(self) -> None:
            return None

    class Client:
        calls = 0

        def get(self, *_args, **_kwargs):
            self.calls += 1
            if self.calls > 1:
                raise AssertionError("processed inbox body must not be fetched again")
            return Response()

    client = Client()
    result = collect_restricted_messages(
        client,
        Child(student_id=1, child="Child", calendar_class_name="001", school=HOUSEHOLD_SCHOOL),
        key=KEY,
        previous_states={previous_state["thread_ref"]: previous_state},
        page_limit=1,
        page_safety_limit=10,
        fetch_changed_content=True,
    )

    assert client.calls == 1
    assert result.changed_threads == []
    assert result.states[0]["has_new_message"] is True
    assert (
        result.states[0]["latest_inbox_message_ref"]
        == previous_state["latest_inbox_message_ref"]
    )


def test_restricted_store_rejects_generic_message_records(tmp_path: Path) -> None:
    message = SourceRecord(
        student_id=1,
        child="Child",
        source_type="messages",
        source_key="thread:raw:message:raw",
        source_url="https://x.skoleintra.dk/messages",
        body_text="CANARY_PRIVATE_BODY",
    )
    check = SourceCheck(1, "Child", "messages", [message])

    with SnapshotStore(
        tmp_path / "school.db"
    ) as store:
        run_id = store.begin_run()
        with pytest.raises(ValueError, match="never stored"):
            store.store_checks(run_id, [check])


def test_restricted_store_refuses_existing_legacy_message_database(
    tmp_path: Path,
) -> None:
    from tests_python.test_dashboard_rendering import plant_legacy_message

    database = tmp_path / "legacy.db"
    plant_legacy_message(database, "CANARY_LEGACY_BODY")
    with pytest.raises(ValueError, match="removed legacy mode"):
        SnapshotStore(database)
    # Refused before anything is migrated or written.
    with pytest.raises(ValueError, match="removed legacy mode"):
        SnapshotStore(database, read_only=True)


def test_current_message_state_is_replaced_without_body_or_full_name(
    tmp_path: Path,
) -> None:
    database = tmp_path / "school.db"
    state = safe_thread_state(
        _conversation(), student_id=1, child="Child", key=KEY
    )
    with SnapshotStore(database) as store:
        store.replace_message_thread_states(
            student_id=1,
            child="Child",
            states=[state],
            complete=True,
        )
        stored = store.message_thread_states()
        assert stored[0]["participants"] == ["John B.", "Anne J."]
        assert stored[0]["is_new_thread"] is True
        assert stored[0]["has_new_message"] is False
        assert stored[0]["latest_unretrieved"] is True
        assert stored[0]["latest_activity_at"] == "2026-09-13T08:15"
        store.mark_message_retrieved(
            thread_ref=stored[0]["thread_ref"],
            message_ref=stored[0]["latest_message_ref"],
        )
        retrieved = store.message_thread_states()[0]
        assert retrieved["has_new_message"] is False
        assert retrieved["latest_unretrieved"] is False

        changed_conversation = {**_conversation(), "latest_message_id": 74}
        changed = safe_thread_state(
            changed_conversation,
            student_id=1,
            child="Child",
            key=KEY,
            previous=retrieved,
        )
        store.replace_message_thread_states(
            student_id=1, child="Child", states=[changed], complete=True
        )
        changed_stored = store.message_thread_states()[0]
        assert changed_stored["is_new_thread"] is False
        assert changed_stored["has_new_message"] is True
        assert changed_stored["latest_unretrieved"] is True

        unchanged = safe_thread_state(
            changed_conversation,
            student_id=1,
            child="Child",
            key=KEY,
            previous=changed_stored,
        )
        store.replace_message_thread_states(
            student_id=1, child="Child", states=[unchanged], complete=True
        )
        unchanged_stored = store.message_thread_states()[0]
        assert unchanged_stored["has_new_message"] is False
        assert unchanged_stored["latest_unretrieved"] is True

        invalid = dict(state)
        invalid["body"] = "CANARY_PRIVATE_BODY"
        with pytest.raises(ValueError, match="unknown fields"):
            store.replace_message_thread_states(
                student_id=1,
                child="Child",
                states=[invalid],
                complete=True,
            )

    durable = b"".join(path.read_bytes() for path in tmp_path.glob("school.db*"))
    assert b"CANARY_PRIVATE_BODY" not in durable
    assert b"John Brown" not in durable


def test_migrates_v1_message_state_without_losing_retrieval_state(
    tmp_path: Path,
) -> None:
    database = tmp_path / "school.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE message_thread_state (
                thread_ref TEXT PRIMARY KEY,
                student_id INTEGER NOT NULL,
                child TEXT NOT NULL,
                participant_display_json TEXT NOT NULL,
                exists_now INTEGER NOT NULL,
                server_unread INTEGER NOT NULL,
                has_new_message INTEGER NOT NULL,
                latest_message_ref TEXT NOT NULL,
                last_retrieved_message_ref TEXT,
                observed_at TEXT NOT NULL,
                policy_version TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO message_thread_state VALUES (
                'thr_aaaaaaaaaaaaaaaa', 1, 'Child', '["John B."]',
                1, 1, 1, 'msg_bbbbbbbbbbbbbbbb', NULL,
                '2026-09-13T08:00:00+00:00', 'restricted-messages-v1'
            )
            """
        )

    with SnapshotStore(database) as store:
        migrated = store.message_thread_states()[0]
        assert migrated["is_new_thread"] is False
        assert migrated["has_new_message"] is False
        assert migrated["latest_unretrieved"] is True
        assert migrated["latest_activity_at"] is None
        assert migrated["policy_version"] == MESSAGE_POLICY_VERSION


def test_migration_backfills_latest_inbox_ref_from_safe_receipt(tmp_path: Path) -> None:
    database = tmp_path / "school.db"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE message_thread_state (
                thread_ref TEXT PRIMARY KEY,
                student_id INTEGER NOT NULL,
                child TEXT NOT NULL,
                participant_display_json TEXT NOT NULL,
                exists_now INTEGER NOT NULL,
                server_unread INTEGER NOT NULL,
                is_new_thread INTEGER NOT NULL,
                has_new_message INTEGER NOT NULL,
                latest_unretrieved INTEGER NOT NULL,
                latest_activity_at TEXT,
                inbox_position INTEGER NOT NULL,
                latest_message_ref TEXT NOT NULL,
                last_retrieved_message_ref TEXT,
                observed_at TEXT NOT NULL,
                policy_version TEXT NOT NULL
            );
            CREATE TABLE message_processing_receipts (
                thread_ref TEXT NOT NULL,
                message_ref TEXT NOT NULL,
                result_code TEXT NOT NULL,
                extractor_version TEXT NOT NULL,
                policy_version TEXT NOT NULL,
                processed_at TEXT NOT NULL,
                PRIMARY KEY(thread_ref, message_ref)
            );
            INSERT INTO message_thread_state VALUES (
                'thr_aaaaaaaaaaaaaaaa', 1, 'Child', '["John B."]',
                1, 0, 0, 0, 1, '2026-09-13T08:00', 0,
                'msg_cccccccccccccccc', NULL, '2026-09-13T08:00:00+00:00',
                'restricted-messages-v2'
            );
            INSERT INTO message_processing_receipts VALUES (
                'thr_aaaaaaaaaaaaaaaa', 'msg_bbbbbbbbbbbbbbbb', 'no_events',
                'message-events-v1', 'restricted-messages-v2',
                '2026-09-13T08:01:00+00:00'
            );
            """
        )

    with SnapshotStore(database) as store:
        migrated = store.message_thread_states()[0]

    assert migrated["latest_inbox_message_ref"] == "msg_bbbbbbbbbbbbbbbb"
    assert migrated["policy_version"] == MESSAGE_POLICY_VERSION


def test_event_projection_discards_evidence_body_and_full_surname(
    tmp_path: Path,
) -> None:
    message = _message()
    thread = EphemeralThread(
        student_id=1,
        child="Child",
        thread_ref="thr_aaaaaaaaaaaaaaaa",
        latest_message_ref="msg_bbbbbbbbbbbbbbbb",
        conversation=_conversation(),
        messages=[message],
    )
    assert "CANARY_PRIVATE_BODY" not in repr(thread)
    with pytest.raises(TypeError, match="cannot be serialized"):
        pickle.dumps(thread)
    output = {
        "items": [
            {
                "dedupe_hint": "library-event-2026-09-18",
                "event_kind": "event",
                "activity_kind": None,
                "celebrant": None,
                "effective_date": "2026-09-18",
                "end_date": None,
                "all_day": True,
                "cancellation_state": "active",
                "necessary_location": "library",
                "bring_items": [],
                "requirement": None,
                "confidence": 0.98,
                "needs_review": False,
                "evidence_quote": "Meeting at library on 18 September.",
            }
        ]
    }

    facts = project_event_facts(
        output, thread=thread, message=message, key=KEY
    )

    assert facts[0]["participant_display_names"] == ["John B.", "Anne J."]
    assert facts[0]["necessary_location"] == "Library"
    assert "evidence" not in facts[0]
    assert "dedupe_hint" not in facts[0]
    assert "CANARY_PRIVATE_BODY" not in json.dumps(facts)
    assert "Brown" not in json.dumps(facts)

    database = tmp_path / "school.db"
    with SnapshotStore(database) as store:
        store.complete_message_event_extraction(
            thread_ref=thread.thread_ref,
            message_ref=thread.latest_message_ref,
            facts=facts,
            extractor_version="message-events-v1",
            policy_version=MESSAGE_POLICY_VERSION,
        )
        items = delivery_items_from_snapshot(store, message_mode="events-only")
        manifest = build_delivery_manifest(
            items,
            target_date=__import__("datetime").date(2026, 9, 18),
            coverage=store.message_coverage(
                mode="events-only", processor="local"
            ),
        )
        assert store.record_delivery_keys(
            [facts[0]["stable_key"]], at="2026-09-13T12:00:00+00:00"
        ) == 1
        delivered = store.message_event_items()[0]
        feedback = store.record_message_event_feedback(
            thread_ref=thread.thread_ref,
            stable_key=facts[0]["stable_key"],
            verdict="correct",
        )
        evaluation = store.message_evaluation_report()

    encoded = json.dumps(manifest, ensure_ascii=False)
    assert "CANARY_PRIVATE_BODY" not in encoded
    assert "Brown" not in encoded
    assert manifest["calendar"][0]["title"] == "Child · Skolearrangement"
    assert delivered["first_delivered_at"] == "2026-09-13T12:00:00+00:00"
    assert feedback["verdict"] == "correct"
    assert evaluation["observed_precision"] == 1.0
    durable = b"".join(path.read_bytes() for path in tmp_path.glob("school.db*"))
    assert b"CANARY_PRIVATE_BODY" not in durable
    assert b"John Brown" not in durable


def test_timed_event_is_retained_as_reviewable_date_fact(tmp_path: Path) -> None:
    message = {
        **_message(),
        "body_text": "CANARY_PRIVATE_BODY Meeting at library on 18 September at 15:00.",
    }
    thread = EphemeralThread(
        student_id=1,
        child="Child",
        thread_ref="thr_aaaaaaaaaaaaaaaa",
        latest_message_ref="msg_bbbbbbbbbbbbbbbb",
        conversation=_conversation(),
        messages=[message],
    )
    output = {
        "items": [
            {
                "dedupe_hint": "library-event-2026-09-18",
                "event_kind": "event",
                "activity_kind": None,
                "celebrant": None,
                "effective_date": "2026-09-18",
                "end_date": None,
                "all_day": False,
                "cancellation_state": "active",
                "necessary_location": "library",
                "bring_items": [],
                "requirement": None,
                "confidence": 0.98,
                "needs_review": False,
                "evidence_quote": "Meeting at library on 18 September at 15:00.",
            }
        ]
    }

    facts = project_event_facts(output, thread=thread, message=message, key=KEY)
    assert facts[0]["all_day"] is False
    assert facts[0]["needs_review"] is True

    with SnapshotStore(tmp_path / "school.db") as store:
        store.complete_message_event_extraction(
            thread_ref=thread.thread_ref,
            message_ref=thread.latest_message_ref,
            facts=facts,
            extractor_version="message-events-v1",
            policy_version=MESSAGE_POLICY_VERSION,
        )
        stored = store.message_event_items()[0]
    assert stored["all_day"] is False
    assert stored["needs_review"] is True


def test_bring_projection_retains_only_operational_objects(tmp_path: Path) -> None:
    message = {
        **_message(),
        "body_text": (
            "CANARY_PRIVATE_BODY Remember a jam jar and paper tomorrow. "
            "Private discussion must not survive."
        ),
    }
    thread = EphemeralThread(
        student_id=1,
        child="Child",
        thread_ref="thr_aaaaaaaaaaaaaaaa",
        latest_message_ref="msg_bbbbbbbbbbbbbbbb",
        conversation=_conversation(),
        messages=[message],
    )
    output = {
        "items": [
            {
                "dedupe_hint": "workshop-bring-2026-09-15",
                "event_kind": "bring",
                "activity_kind": None,
                "celebrant": None,
                "effective_date": "2026-09-15",
                "end_date": None,
                "all_day": True,
                "cancellation_state": "active",
                "necessary_location": None,
                "bring_items": ["jam jar", "paper"],
                "requirement": "required",
                "confidence": 0.99,
                "needs_review": False,
                "evidence_quote": "Remember a jam jar and paper tomorrow.",
            }
        ]
    }

    facts = project_event_facts(output, thread=thread, message=message, key=KEY)

    assert facts[0]["event_kind"] == "bring"
    assert facts[0]["bring_items"] == ["jam jar", "paper"]
    assert facts[0]["requirement"] == "required"
    assert "CANARY_PRIVATE_BODY" not in json.dumps(facts)
    assert "Private discussion" not in json.dumps(facts)

    database = tmp_path / "school.db"
    with SnapshotStore(database) as store:
        store.complete_message_event_extraction(
            thread_ref=thread.thread_ref,
            message_ref=thread.latest_message_ref,
            facts=facts,
            extractor_version="message-events-v2",
            policy_version=MESSAGE_POLICY_VERSION,
        )
        stored = store.message_event_items()[0]

    assert stored["item_type"] == "bring"
    assert stored["stream"] == "actions"
    assert stored["title"] == "Medbring jam jar og paper"
    assert stored["actionability"] == "required"
    assert stored["summary"] == "Skal med tir. 15. sep. Fra John B., Anne J."
    durable = b"".join(path.read_bytes() for path in tmp_path.glob("school.db*"))
    assert b"CANARY_PRIVATE_BODY" not in durable
    assert b"Private discussion" not in durable
    assert b"John Brown" not in durable


def test_birthday_invitation_keeps_its_kind_and_only_the_birthday_childs_first_name(tmp_path: Path) -> None:
    """restricted-messages-v5: a birthday party keeps whose party it is, and nothing else."""
    message = {
        **_message(),
        "body_text": "CANARY_PRIVATE_BODY Clara inviterer til fødselsdag søndag den 1. november.",
    }
    thread = EphemeralThread(
        student_id=1,
        child="Child",
        thread_ref="thr_aaaaaaaaaaaaaaaa",
        latest_message_ref="msg_bbbbbbbbbbbbbbbb",
        conversation=_conversation(),
        messages=[message],
    )
    item = {
        "dedupe_hint": "birthday-party-2026-11-01",
        "event_kind": "event",
        "activity_kind": "birthday_party",
        "celebrant": "Clara",
        "effective_date": "2026-11-01",
        "end_date": None,
        "all_day": True,
        "cancellation_state": "active",
        "necessary_location": None,
        "bring_items": [],
        "requirement": None,
        "confidence": 0.97,
        "needs_review": False,
        "evidence_quote": "inviterer til fødselsdag søndag den 1. november",
    }
    facts = project_event_facts({"items": [item]}, thread=thread, message=message, key=KEY)
    assert facts[0]["activity_kind"] == "birthday_party" and facts[0]["celebrant"] == "Clara"

    with SnapshotStore(tmp_path / "school.db") as store:
        store.complete_message_event_extraction(
            thread_ref=thread.thread_ref,
            message_ref=thread.latest_message_ref,
            facts=facts,
            extractor_version="message-events-v4",
            policy_version=MESSAGE_POLICY_VERSION,
        )
        stored = store.message_event_items()[0]
        assert store.connection.execute(
            "SELECT celebrant FROM message_event_facts").fetchone()[0] == "Clara"
    assert stored["title"] == "Claras fødselsdag"
    assert stored["activity_kind"] == "birthday_party" and stored["celebrant"] == "Clara"
    durable = b"".join(path.read_bytes() for path in tmp_path.glob("school.db*"))
    assert b"CANARY_PRIVATE_BODY" not in durable
    assert b"inviterer" not in durable and b"1. november" not in durable

    bring = {**item, "event_kind": "bring", "celebrant": None, "bring_items": ["gave"], "requirement": "required"}
    with pytest.raises(MessageEventExtractionError, match="activity_kind_on_non_event_fact"):
        project_event_facts({"items": [bring]}, thread=thread, message=message, key=KEY)
    trip = {**item, "activity_kind": "class_trip"}
    with pytest.raises(MessageEventExtractionError, match="celebrant_on_non_birthday_fact"):
        project_event_facts({"items": [trip]}, thread=thread, message=message, key=KEY)


@pytest.mark.parametrize("body, celebrant, kept", [
    ("Emma inviterer til fødselsdag søndag den 1. november.", "Emma", "Emma"),
    ("Kom til Emmas fødselsdag søndag den 1. november.", "Emma", "Emma"),
    ("Kom til Ella-Rose fødselsdag søndag den 1. november.", "Ella-Rose", "Ella-Rose"),
    # Not in the message: the model made it up.
    ("Der er fødselsdag søndag den 1. november.", "Emma", None),
    # Not one capitalised first name.
    ("emma inviterer til fødselsdag søndag den 1. november.", "emma", None),
    ("Emma2 inviterer til fødselsdag søndag den 1. november.", "Emma2", None),
    ("Emma Hansen inviterer til fødselsdag søndag den 1. november.", "Emma Hansen", None),
    ("Emma Hansen inviterer til fødselsdag søndag den 1. november.", "Emma", "Emma"),
    # The birthday child's surname is the surname half of a full name.
    ("Emma Hansen inviterer til fødselsdag søndag den 1. november.", "Hansen", None),
    ("Kom til Emma Hansens fødselsdag søndag den 1. november.", "Hansen", None),
    ("Kom til Emma\nHansens fødselsdag søndag den 1. november.", "Hansen", None),
    # A greeting is no name: 'Kære Emma' still names Emma.
    ("Kære Emma. Du inviteres til fødselsdag søndag den 1. november.", "Emma", "Emma"),
    # A surname of a participant, the sender (an adult), or the family's own child.
    ("Familien Brown inviterer til fødselsdag søndag den 1. november.", "Brown", None),
    ("Mvh John: Emma inviterer til fødselsdag søndag den 1. november.", "John", None),
    ("Child inviterer til fødselsdag søndag den 1. november.", "Child", None),
])
def test_only_a_plain_birthday_childs_name_is_kept(body, celebrant, kept) -> None:
    message = {**_message(), "body_text": body}
    thread = EphemeralThread(student_id=1, child="Child", thread_ref="thr_aaaaaaaaaaaaaaaa",
                             latest_message_ref="msg_bbbbbbbbbbbbbbbb", conversation=_conversation(),
                             messages=[message])
    item = {"dedupe_hint": "birthday-party-2026-11-01", "event_kind": "event",
            "activity_kind": "birthday_party", "celebrant": celebrant, "effective_date": "2026-11-01",
            "end_date": None, "all_day": True, "cancellation_state": "active", "necessary_location": None,
            "bring_items": [], "requirement": None, "confidence": 0.97, "needs_review": False,
            "evidence_quote": "fødselsdag søndag den 1. november"}
    [fact] = project_event_facts({"items": [item]}, thread=thread, message=message, key=KEY)
    # A refused name costs only the name: the party stays, with its plain title.
    assert fact["activity_kind"] == "birthday_party" and fact["celebrant"] == kept


def test_bring_projection_rejects_full_participant_surname() -> None:
    message = {
        **_message(),
        "body_text": "Bring Brown folder on 15 September.",
    }
    thread = EphemeralThread(
        student_id=1,
        child="Child",
        thread_ref="thr_aaaaaaaaaaaaaaaa",
        latest_message_ref="msg_bbbbbbbbbbbbbbbb",
        conversation=_conversation(),
        messages=[message],
    )
    output = {
        "items": [
            {
                "dedupe_hint": "bring-folder-2026-09-15",
                "event_kind": "bring",
                "activity_kind": None,
                "celebrant": None,
                "effective_date": "2026-09-15",
                "end_date": None,
                "all_day": True,
                "cancellation_state": "active",
                "necessary_location": None,
                "bring_items": ["Brown folder"],
                "requirement": "required",
                "confidence": 0.9,
                "needs_review": False,
                "evidence_quote": "Bring Brown folder on 15 September.",
            }
        ]
    }

    with pytest.raises(MessageEventExtractionError, match="bring_items_private_name"):
        project_event_facts(output, thread=thread, message=message, key=KEY)


def test_processing_receipt_is_stale_when_contract_version_changes(
    tmp_path: Path,
) -> None:
    with SnapshotStore(tmp_path / "school.db") as store:
        store.complete_message_event_extraction(
            thread_ref="thr_aaaaaaaaaaaaaaaa",
            message_ref="msg_bbbbbbbbbbbbbbbb",
            facts=[],
            extractor_version="message-events-v1",
            policy_version="restricted-messages-v3",
        )

        assert store.message_processing_receipt(
            thread_ref="thr_aaaaaaaaaaaaaaaa",
            message_ref="msg_bbbbbbbbbbbbbbbb",
        )
        assert store.message_processing_receipt(
            thread_ref="thr_aaaaaaaaaaaaaaaa",
            message_ref="msg_bbbbbbbbbbbbbbbb",
            extractor_version="message-events-v1",
            policy_version="restricted-messages-v3",
        )
        assert store.message_processing_receipt(
            thread_ref="thr_aaaaaaaaaaaaaaaa",
            message_ref="msg_bbbbbbbbbbbbbbbb",
            extractor_version="message-events-v2",
            policy_version="restricted-messages-v4",
        ) is None


def test_new_extractor_supersedes_prior_facts_for_same_message(tmp_path: Path) -> None:
    event_fact = {
        "stable_key": "message-event:" + "a" * 64,
        "child": "Child",
        "event_kind": "event",
        "activity_kind": None,
        "celebrant": None,
        "effective_date": "2026-09-15",
        "end_date": None,
        "all_day": True,
        "cancellation_state": "active",
        "necessary_location": None,
        "bring_items": [],
        "requirement": None,
        "participant_display_names": ["John B."],
        "template_key": "private_event",
        "confidence": 0.9,
        "needs_review": False,
        "workflow_state": "active",
    }
    bring_fact = {
        **event_fact,
        "stable_key": "message-event:" + "b" * 64,
        "event_kind": "bring",
        "activity_kind": None,
        "celebrant": None,
        "bring_items": ["jam jar", "paper"],
        "requirement": "required",
        "template_key": "private_bring",
    }

    database = tmp_path / "school.db"
    with SnapshotStore(database) as store:
        store.complete_message_event_extraction(
            thread_ref="thr_aaaaaaaaaaaaaaaa",
            message_ref="msg_bbbbbbbbbbbbbbbb",
            facts=[event_fact],
            extractor_version="message-events-v1",
            policy_version="restricted-messages-v3",
        )
        store.complete_message_event_extraction(
            thread_ref="thr_aaaaaaaaaaaaaaaa",
            message_ref="msg_bbbbbbbbbbbbbbbb",
            facts=[bring_fact],
            extractor_version="message-events-v2",
            policy_version="restricted-messages-v4",
        )
        active = store.message_event_items()
        prior_state = store.connection.execute(
            "SELECT workflow_state FROM message_event_facts WHERE stable_key = ?",
            (event_fact["stable_key"],),
        ).fetchone()["workflow_state"]

    assert [item["item_type"] for item in active] == ["bring"]
    assert prior_state == "superseded"


def test_evidence_validation_accepts_unicode_punctuation_equivalents() -> None:
    message = {
        **_message(),
        "body_text": "CANARY_PRIVATE_BODY Møde – fredag kl. 15.00.",
    }
    thread = EphemeralThread(
        student_id=1,
        child="Child",
        thread_ref="thr_aaaaaaaaaaaaaaaa",
        latest_message_ref="msg_bbbbbbbbbbbbbbbb",
        conversation=_conversation(),
        messages=[message],
    )
    output = {
        "items": [
            {
                "dedupe_hint": "meeting-2026-09-18",
                "event_kind": "event",
                "activity_kind": None,
                "celebrant": None,
                "effective_date": "2026-09-18",
                "end_date": None,
                "all_day": False,
                "cancellation_state": "active",
                "necessary_location": None,
                "bring_items": [],
                "requirement": None,
                "confidence": 0.9,
                "needs_review": True,
                "evidence_quote": "Møde - fredag kl. 15.00",
            }
        ]
    }

    assert project_event_facts(output, thread=thread, message=message, key=KEY)


def test_zdr_processor_retries_truncated_output_and_records_safe_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeOpenRouterClient:
        calls: list[int] = []

        def __init__(self, **_kwargs) -> None:
            pass

        def generate(self, _document, *, manifest, max_output_tokens):
            assert manifest.manifest_id == "message-events-v4"
            self.calls.append(max_output_tokens)
            if len(self.calls) == 1:
                raise OpenRouterGenerationError(
                    stage="model_output_decode",
                    error_code="max_output_tokens",
                    response_id="gen-first",
                    usage={"total_tokens": 2500, "cost": 0.01},
                    finish_reason="length",
                )
            return {
                "response_id": "gen-second",
                "usage": {"total_tokens": 100, "cost": 0.001},
                "output": {"items": []},
            }

    monkeypatch.setattr(
        "skoleklar.triage.message_events.OpenRouterClient", FakeOpenRouterClient
    )
    message = _message()
    thread = EphemeralThread(
        student_id=1,
        child="Child",
        thread_ref="thr_aaaaaaaaaaaaaaaa",
        latest_message_ref="msg_bbbbbbbbbbbbbbbb",
        conversation=_conversation(),
        messages=[message],
    )
    message_ref = message_handle(message, student_id=1, key=KEY)

    with SnapshotStore(tmp_path / "school.db") as store:
        result = process_changed_threads(
            store,
            [thread],
            key=KEY,
            processor="zdr",
            model="test-model",
            api_key="test-key",
        )
        receipt = store.message_processing_receipt(
            thread_ref=thread.thread_ref, message_ref=message_ref
        )
        attempt = store.message_processing_attempt(
            thread_ref=thread.thread_ref, message_ref=message_ref
        )

    assert FakeOpenRouterClient.calls == [2500, 5000]
    assert result["status"] == "complete"
    assert result["attempt_count"] == 2
    assert result["retry_count"] == 1
    assert result["failure_count"] == 0
    assert result["usage"]["total_tokens"] == 2600
    assert receipt is not None
    assert attempt["status"] == "succeeded"
    assert attempt["response_id"] == "gen-second"
    assert attempt["total_tokens"] == 2600
    assert attempt["cost"] == pytest.approx(0.011)


def test_zdr_processor_retries_invalid_evidence_without_persisting_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeOpenRouterClient:
        calls = 0

        def __init__(self, **_kwargs) -> None:
            pass

        def generate(self, document, **_kwargs):
            type(self).calls += 1
            quote = (
                "Meeting at library on 18 September."
                if type(self).calls == 2
                else "Paraphrased private evidence"
            )
            if type(self).calls == 2:
                assert "validation_feedback" in document
            return {
                "response_id": f"gen-{type(self).calls}",
                "usage": {},
                "output": {
                    "items": [
                        {
                            "dedupe_hint": "library-event-2026-09-18",
                            "event_kind": "event",
                            "activity_kind": None,
                            "celebrant": None,
                            "effective_date": "2026-09-18",
                            "end_date": None,
                            "all_day": True,
                            "cancellation_state": "active",
                            "necessary_location": "library",
                            "bring_items": [],
                            "requirement": None,
                            "confidence": 0.98,
                            "needs_review": False,
                            "evidence_quote": quote,
                        }
                    ]
                },
            }

    monkeypatch.setattr(
        "skoleklar.triage.message_events.OpenRouterClient", FakeOpenRouterClient
    )
    message = _message()
    thread = EphemeralThread(
        student_id=1,
        child="Child",
        thread_ref="thr_aaaaaaaaaaaaaaaa",
        latest_message_ref="msg_bbbbbbbbbbbbbbbb",
        conversation=_conversation(),
        messages=[message],
    )

    database = tmp_path / "school.db"
    with SnapshotStore(database) as store:
        result = process_changed_threads(
            store,
            [thread],
            key=KEY,
            processor="zdr",
            model="test-model",
            api_key="test-key",
        )

    assert FakeOpenRouterClient.calls == 2
    assert result["retry_count"] == 1
    assert result["failure_count"] == 0
    durable = b"".join(path.read_bytes() for path in tmp_path.glob("school.db*"))
    assert b"CANARY_PRIVATE_BODY" not in durable
    assert b"Paraphrased private evidence" not in durable


def test_zdr_processor_persists_only_content_free_final_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeOpenRouterClient:
        calls = 0

        def __init__(self, **_kwargs) -> None:
            pass

        def generate(self, _document, **_kwargs):
            type(self).calls += 1
            return {
                "response_id": f"gen-{type(self).calls}",
                "usage": {"total_tokens": 10, "cost": 0.001},
                "output": {
                    "items": [
                        {
                            "dedupe_hint": "library-event-2026-09-18",
                            "event_kind": "event",
                            "activity_kind": None,
                            "celebrant": None,
                            "effective_date": "2026-09-18",
                            "end_date": None,
                            "all_day": True,
                            "cancellation_state": "active",
                            "necessary_location": None,
                            "bring_items": [],
                            "requirement": None,
                            "confidence": 0.9,
                            "needs_review": False,
                            "evidence_quote": "Paraphrased private evidence",
                        }
                    ]
                },
            }

    monkeypatch.setattr(
        "skoleklar.triage.message_events.OpenRouterClient", FakeOpenRouterClient
    )
    message = _message()
    thread = EphemeralThread(
        student_id=1,
        child="Child",
        thread_ref="thr_aaaaaaaaaaaaaaaa",
        latest_message_ref="msg_bbbbbbbbbbbbbbbb",
        conversation=_conversation(),
        messages=[message],
    )
    message_ref = message_handle(message, student_id=1, key=KEY)
    database = tmp_path / "school.db"

    with SnapshotStore(database) as store:
        result = process_changed_threads(
            store,
            [thread],
            key=KEY,
            processor="zdr",
            model="test-model",
            api_key="test-key",
        )
        attempt = store.message_processing_attempt(
            thread_ref=thread.thread_ref, message_ref=message_ref
        )

    assert result["status"] == "partial"
    assert result["failure_count"] == 1
    assert result["failure_reasons"] == {"event_evidence_invalid": 1}
    assert result["failures"][0]["stage"] == "projection"
    assert "CANARY_PRIVATE_BODY" not in json.dumps(result)
    assert attempt["status"] == "failed"
    assert attempt["stage"] == "projection"
    assert attempt["error_code"] == "event_evidence_invalid"
    durable = b"".join(path.read_bytes() for path in tmp_path.glob("school.db*"))
    assert b"CANARY_PRIVATE_BODY" not in durable
    assert b"Paraphrased private evidence" not in durable


def test_zdr_processor_uses_bounded_concurrency_and_safe_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeOpenRouterClient:
        active = 0
        max_active = 0
        lock = threading.Lock()

        def __init__(self, **_kwargs) -> None:
            pass

        def generate(self, _document, **_kwargs):
            with type(self).lock:
                type(self).active += 1
                type(self).max_active = max(type(self).max_active, type(self).active)
            time.sleep(0.04)
            with type(self).lock:
                type(self).active -= 1
            return {
                "response_id": "gen-safe",
                "usage": {"total_tokens": 10, "cost": 0.001},
                "output": {"items": []},
            }

    monkeypatch.setattr(
        "skoleklar.triage.message_events.OpenRouterClient", FakeOpenRouterClient
    )
    threads = []
    for index, letter in enumerate(("a", "b", "c"), start=1):
        message = {**_message(), "message_id": 70 + index}
        conversation = {
            **_conversation(),
            "thread_id": 40 + index,
            "latest_message_id": 70 + index,
            "latest_inbox_message_id": 70 + index,
        }
        threads.append(
            EphemeralThread(
                student_id=1,
                child="Child",
                thread_ref=f"thr_{letter * 16}",
                latest_message_ref=f"msg_{letter * 16}",
                conversation=conversation,
                messages=[message],
            )
        )

    progress: list[dict] = []
    with SnapshotStore(tmp_path / "school.db") as store:
        result = process_changed_threads(
            store,
            threads,
            key=KEY,
            processor="zdr",
            model="test-model",
            api_key="test-key",
            max_concurrency=2,
            progress_callback=progress.append,
        )

    assert FakeOpenRouterClient.max_active == 2
    assert result["concurrency"] == 2
    assert result["processed_threads"] == 3
    assert [event["completed"] for event in progress] == [1, 2, 3]
    assert progress[-1]["total"] == 3
    assert "CANARY_PRIVATE_BODY" not in json.dumps(progress)


def test_cli_exposes_privacy_message_controls() -> None:
    parser = build_parser()
    harvest = parser.parse_args(
        [
            "harvest",
            "--message-mode",
            "events-only",
            "--message-processor",
            "zdr",
        ]
    )
    listing = parser.parse_args(
        ["message", "list", "--refresh", "--message-mode", "events-only"]
    )
    retrieval = parser.parse_args(
        [
            "message",
            "get",
            "thr_aaaaaaaaaaaaaaaa",
            "--message",
            "msg_bbbbbbbbbbbbbbbb",
            "--format",
            "json",
            "--message-mode",
            "events-only",
        ]
    )
    latest_child = parser.parse_args(
        [
            "message",
            "get",
            "--child",
            "Beta",
            "--latest",
            "--message-mode",
            "events-only",
        ]
    )
    review = parser.parse_args(
        [
            "message",
            "review",
            "thr_aaaaaaaaaaaaaaaa",
            "missed_event",
            "--message-mode",
            "events-only",
        ]
    )

    assert harvest.message_mode == "events-only"
    assert harvest.message_processor == "zdr"
    assert listing.refresh is True
    assert retrieval.message_ref == "msg_bbbbbbbbbbbbbbbb"
    assert latest_child.thread_ref is None
    assert latest_child.child == "Beta"
    assert latest_child.latest is True
    assert review.verdict == "missed_event"


def test_re_extraction_under_v4_keeps_the_partys_identity_and_workflow(tmp_path: Path) -> None:
    """The stable key comes from the thread, kind and hint, not the prompt: acknowledgements survive."""
    message = {**_message(), "body_text": "Clara inviterer til fødselsdag søndag den 1. november."}
    thread = EphemeralThread(student_id=1, child="Child", thread_ref="thr_aaaaaaaaaaaaaaaa",
                             latest_message_ref="msg_bbbbbbbbbbbbbbbb", conversation=_conversation(),
                             messages=[message])
    item = {"dedupe_hint": "birthday-party-2026-11-01", "event_kind": "event", "activity_kind": "birthday_party",
            "celebrant": None, "effective_date": "2026-11-01", "end_date": None, "all_day": True,
            "cancellation_state": "active", "necessary_location": None, "bring_items": [], "requirement": None,
            "confidence": 0.97, "needs_review": False, "evidence_quote": "inviterer til fødselsdag"}
    before = project_event_facts({"items": [item]}, thread=thread, message=message, key=KEY)
    after = project_event_facts({"items": [{**item, "celebrant": "Clara"}]}, thread=thread, message=message,
                                key=KEY)
    assert before[0]["stable_key"] == after[0]["stable_key"]
    with SnapshotStore(tmp_path / "school.db") as store:
        refs = {"thread_ref": thread.thread_ref, "message_ref": thread.latest_message_ref,
                "policy_version": MESSAGE_POLICY_VERSION}
        store.complete_message_event_extraction(facts=before, extractor_version="message-events-v3:old", **refs)
        store.connection.execute("UPDATE item_workflow SET acknowledged_at = '2026-10-28T08:00:00Z'")
        store.connection.commit()
        store.complete_message_event_extraction(facts=after, extractor_version="message-events-v4:new", **refs)
        [party] = store.message_event_items()
    assert party["title"] == "Claras fødselsdag" and party["stable_key"] == before[0]["stable_key"]
    assert party["acknowledged_at"] == "2026-10-28T08:00:00Z"
