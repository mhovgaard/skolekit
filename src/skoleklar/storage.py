from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import stat
from pathlib import Path
from importlib.resources import files
from typing import Any

from skoleklar.restricted_messages import MESSAGE_POLICY_VERSION
from skoleklar.migrations import migrate
from skoleklar.store.capture import CaptureRepository
from skoleklar.store.delivery import DeliveryRepository
from skoleklar.store.facts import FactRepository
from skoleklar.store.facts import source_date_from_payload  # noqa: F401  (kept for callers)
from skoleklar.store.message_facts import MessageFactRepository
from skoleklar.store.summaries import SummaryRepository
from skolekit.records import calendar_source_key


# The schema as it stood before numbered migrations. New databases get it
# from migration 0001; this copy drives the startup upgrade of older files.
SCHEMA = (files("skoleklar.migrations") / "0001_baseline.sql").read_text(encoding="utf-8")


def _legacy_upgrade(connection: sqlite3.Connection) -> None:
    """The startup upgrade used before numbered migrations; runs only on older files."""
    connection.executescript(SCHEMA)
    message_state_columns = {
        row["name"]
        for row in connection.execute(
            "PRAGMA table_info(message_thread_state)"
        )
    }
    added_latest_unretrieved = "latest_unretrieved" not in message_state_columns
    added_latest_inbox_ref = (
        "latest_inbox_message_ref" not in message_state_columns
    )
    for column, declaration in (
        ("is_new_thread", "INTEGER NOT NULL DEFAULT 0"),
        ("latest_unretrieved", "INTEGER NOT NULL DEFAULT 0"),
        ("latest_activity_at", "TEXT"),
        ("inbox_position", "INTEGER NOT NULL DEFAULT 0"),
        ("latest_inbox_message_ref", "TEXT"),
    ):
        if column not in message_state_columns:
            connection.execute(
                f"ALTER TABLE message_thread_state ADD COLUMN {column} {declaration}"
            )
    if added_latest_unretrieved:
        connection.execute(
            """
            UPDATE message_thread_state
            SET latest_unretrieved = has_new_message,
                has_new_message = 0,
                is_new_thread = 0,
                policy_version = ?
            """,
            (MESSAGE_POLICY_VERSION,),
        )
    if added_latest_inbox_ref:
        connection.execute(
            """
            UPDATE message_thread_state
            SET latest_inbox_message_ref = (
                    SELECT receipt.message_ref
                    FROM message_processing_receipts receipt
                    WHERE receipt.thread_ref = message_thread_state.thread_ref
                    ORDER BY receipt.processed_at DESC
                    LIMIT 1
                ),
                policy_version = ?
            """,
            (MESSAGE_POLICY_VERSION,),
        )
    message_event_columns = {
        row["name"]
        for row in connection.execute(
            "PRAGMA table_info(message_event_facts)"
        )
    }
    for column, declaration in (
        ("bring_items_json", "TEXT NOT NULL DEFAULT '[]'"),
        ("requirement", "TEXT"),
        ("activity_kind", "TEXT"),
    ):
        if column not in message_event_columns:
            connection.execute(
                f"ALTER TABLE message_event_facts ADD COLUMN {column} {declaration}"
            )
    derivation_columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(derivations)")
    }
    if "purpose" not in derivation_columns:
        connection.execute(
            "ALTER TABLE derivations "
            "ADD COLUMN purpose TEXT NOT NULL DEFAULT 'live'"
        )
    workflow_columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(item_workflow)")
    }
    for column, declaration in (
        ("archived_at", "TEXT"),
        ("archive_reason", "TEXT"),
        ("archive_run_id", "INTEGER"),
        ("archive_reviewed_at", "TEXT"),
        ("archive_review_as_of", "TEXT"),
        ("archive_review_reason", "TEXT"),
    ):
        if column not in workflow_columns:
            connection.execute(
                f"ALTER TABLE item_workflow ADD COLUMN {column} {declaration}"
            )


class SnapshotStore(CaptureRepository, FactRepository, MessageFactRepository, SummaryRepository,
                    DeliveryRepository):
    """The household's one database, its layers' repositories on one object (skoleklar/store/)."""

    def __init__(self, path: Path, *, read_only: bool = False, immutable: bool = False):
        """Open the database, or with `read_only` only read it.

        `immutable` is for a copy that nothing writes while it is open, such as
        one from `db pull`: SQLite then takes no locks and creates no `-wal` or
        `-shm` file beside it, so reading leaves the copy's folder as it was.
        """
        if immutable and not read_only:
            raise ValueError("immutable is for read-only opens")
        self.path = path
        flags = "mode=ro&immutable=1" if immutable else "mode=ro"
        # Message text is never stored. A database from the removed legacy mode
        # holds messages as records, so it is refused, never opened or migrated.
        if path.exists():
            with contextlib.closing(sqlite3.connect(f"{path.resolve().as_uri()}?{flags}", uri=True)) as inspection:
                records_table = inspection.execute(
                    "SELECT 1 FROM sqlite_master "
                    "WHERE type = 'table' AND name = 'records'"
                ).fetchone()
                legacy_message = (
                    inspection.execute(
                        "SELECT 1 FROM records WHERE source_type = 'messages' LIMIT 1"
                    ).fetchone()
                    if records_table is not None
                    else None
                )
            if legacy_message is not None:
                raise ValueError(
                    "This database holds message text from the removed legacy mode; "
                    "it is refused, so the text is never read or copied"
                )
        if read_only:
            # Rendering reads an existing schema; it must not migrate, chmod,
            # create a database, or mutate workflow/delivery state.
            self.connection = sqlite3.connect(f"{path.resolve().as_uri()}?{flags}", uri=True)
            self.connection.row_factory = sqlite3.Row
            self.connection.execute("PRAGMA query_only = ON")
            return
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.parent.chmod(stat.S_IRWXU)
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        migrate(self.connection, legacy_upgrade=_legacy_upgrade)
        # Repairs below are idempotent and run on every writable open.
        self.connection.execute(
            """
            UPDATE message_event_facts
            SET workflow_state = 'superseded'
            WHERE workflow_state IN ('active', 'cancelled')
              AND EXISTS (
                  SELECT 1
                  FROM message_processing_receipts receipt
                  WHERE receipt.thread_ref = message_event_facts.thread_ref
                    AND receipt.message_ref = message_event_facts.message_ref
                    AND receipt.extractor_version != message_event_facts.extractor_version
              )
            """
        )
        # An agent's latest 'retain' marks a workflow item as reviewed (item_archive).
        self.connection.execute(
            """
            INSERT INTO item_archive(stable_key, reviewed_at, review_as_of, review_reason)
            SELECT w.stable_key,
                   (SELECT run.completed_at
                    FROM archival_decisions decision
                    JOIN archival_runs run ON run.id = decision.run_id
                    WHERE decision.stable_key = w.stable_key
                      AND decision.method = 'agent' AND decision.decision = 'retain'
                    ORDER BY run.id DESC LIMIT 1),
                   (SELECT run.as_of
                    FROM archival_decisions decision
                    JOIN archival_runs run ON run.id = decision.run_id
                    WHERE decision.stable_key = w.stable_key
                      AND decision.method = 'agent' AND decision.decision = 'retain'
                    ORDER BY run.id DESC LIMIT 1),
                   (SELECT decision.reason
                    FROM archival_decisions decision
                    JOIN archival_runs run ON run.id = decision.run_id
                    WHERE decision.stable_key = w.stable_key
                      AND decision.method = 'agent' AND decision.decision = 'retain'
                    ORDER BY run.id DESC LIMIT 1)
            FROM item_workflow w
            WHERE EXISTS (
                      SELECT 1 FROM archival_decisions decision
                      WHERE decision.stable_key = w.stable_key
                        AND decision.method = 'agent' AND decision.decision = 'retain')
              AND NOT EXISTS (
                      SELECT 1 FROM item_archive a
                      WHERE a.stable_key = w.stable_key AND a.reviewed_at IS NOT NULL)
            ON CONFLICT(stable_key) DO UPDATE SET
                reviewed_at = excluded.reviewed_at, review_as_of = excluded.review_as_of,
                review_reason = excluded.review_reason
            """
        )
        self._migrate_calendar_lesson_keys()
        self.connection.execute(
            """
            INSERT OR IGNORE INTO derivation_sources(
                derivation_id, record_id, version_id
            )
            SELECT DISTINCT i.derivation_id, s.record_id, s.version_id
            FROM derived_item_sources s
            JOIN derived_items i ON i.id = s.item_id
            """
        )
        self.connection.commit()
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)

    def _migrate_calendar_lesson_keys(self) -> None:
        rows = self.connection.execute(
            """
            SELECT r.id, r.student_id, v.payload_json
            FROM records r
            JOIN record_versions v ON v.id = r.current_version_id
            WHERE r.source_type = 'calendar'
              AND r.status = 'active'
              AND r.source_key LIKE 'LessonsEvents:%'
              AND r.source_key NOT LIKE 'LessonsEvents:slot:%'
            """
        ).fetchall()
        updates: list[tuple[str, int]] = []
        seen = {
            (int(row["student_id"]), row["source_key"])
            for row in self.connection.execute(
                """
                SELECT student_id, source_key
                FROM records
                WHERE source_type = 'calendar'
                  AND status = 'active'
                  AND source_key LIKE 'LessonsEvents:slot:%'
                """
            )
        }
        for row in rows:
            payload = json.loads(row["payload_json"])
            raw = payload.get("raw_payload") or {}
            if not isinstance(raw, dict):
                continue
            source_key = calendar_source_key("LessonsEvents", raw)
            if not source_key.startswith("LessonsEvents:slot:"):
                continue
            identity = (int(row["student_id"]), source_key)
            if identity in seen:
                raise RuntimeError("Stable lesson identity collision during migration")
            seen.add(identity)
            updates.append((source_key, int(row["id"])))
        self.connection.executemany(
            "UPDATE records SET source_key = ? WHERE id = ?",
            updates,
        )

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> SnapshotStore:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()


    def structure_report(self, run_id: int | None = None) -> dict[str, Any]:
        if run_id is None:
            row = self.connection.execute(
                "SELECT id FROM harvest_runs WHERE status != 'running' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if row is None:
                raise ValueError("No completed harvest run exists")
            run_id = int(row["id"])
        rows = self.connection.execute(
            """
            SELECT s.child, s.source_type, s.status, s.complete, s.record_count,
                   COUNT(DISTINCT o.version_id) AS versions,
                   COALESCE(SUM(LENGTH(v.body_text)), 0) AS text_characters,
                   COALESCE(SUM(LENGTH(v.payload_json)), 0) AS payload_characters
            FROM source_checks s
            LEFT JOIN records r
              ON r.student_id = s.student_id AND r.source_type = s.source_type
            LEFT JOIN run_observations o
              ON o.run_id = s.run_id AND o.record_id = r.id
            LEFT JOIN record_versions v ON v.id = o.version_id
            WHERE s.run_id = ?
            GROUP BY s.id
            ORDER BY s.child, s.source_type
            """,
            (run_id,),
        ).fetchall()
        return {
            "run_id": run_id,
            "database_bytes": os.path.getsize(self.path),
            "sources": [dict(row) for row in rows],
        }
