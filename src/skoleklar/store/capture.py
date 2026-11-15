"""Layer 1, capture: harvest runs, source checks, records and their versions.

The product owns captured published pages and append-only versions.
Restricted messages use a separate current projection and never enter this
source-record model. See docs/data-foundation.md.
"""
from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from datetime import UTC, date, datetime
from typing import Any, Iterable

from skolekit.records import SourceCheck, SourceRecord


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class CaptureRepository:
    """The capture tables (layer 1) of one profile's database."""

    connection: sqlite3.Connection

    def begin_run(self) -> int:
        cursor = self.connection.execute(
            "INSERT INTO harvest_runs(started_at, status, schema_version) "
            "VALUES (?, 'running', 1)",
            (utc_now(),),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def _insert_version(
        self, run_id: int, record_id: int, record: SourceRecord
    ) -> int:
        value = record.as_dict()
        cursor = self.connection.execute(
            """
            INSERT INTO record_versions(
                record_id, run_id, observed_at, raw_hash, content_hash,
                state_hash, title, body_text, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record_id,
                run_id,
                utc_now(),
                value["raw_hash"],
                value["content_hash"],
                value["state_hash"],
                record.title,
                record.body_text,
                json.dumps(value, ensure_ascii=False, separators=(",", ":")),
            ),
        )
        return int(cursor.lastrowid)

    def _store_record(self, run_id: int, record: SourceRecord) -> str:
        if record.source_type == "messages":
            raise ValueError("Message text is never stored: a message can't be a record")
        hashes = record.hashes()
        existing = self.connection.execute(
            """
            SELECT r.*, v.raw_hash, v.content_hash, v.state_hash
            FROM records r
            LEFT JOIN record_versions v ON v.id = r.current_version_id
            WHERE r.student_id = ? AND r.source_type = ? AND r.source_key = ?
            """,
            record.identity(),
        ).fetchone()

        previous_version_id: int | None = None
        if existing is None:
            cursor = self.connection.execute(
                """
                INSERT INTO records(
                    student_id, child, source_type, source_key, source_url,
                    first_seen_run_id, last_seen_run_id, status, missing_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', 0)
                """,
                (
                    record.student_id,
                    record.child,
                    record.source_type,
                    record.source_key,
                    record.source_url,
                    run_id,
                    run_id,
                ),
            )
            record_id = int(cursor.lastrowid)
            version_id = self._insert_version(run_id, record_id, record)
            change_type = "new"
        else:
            record_id = int(existing["id"])
            previous_version_id = existing["current_version_id"]
            if existing["content_hash"] != hashes["content_hash"]:
                change_type = "content_changed"
            elif existing["state_hash"] != hashes["state_hash"]:
                change_type = "state_changed"
            elif existing["raw_hash"] != hashes["raw_hash"]:
                change_type = "raw_changed"
            else:
                change_type = "unchanged"

            if change_type in {"unchanged", "raw_changed"}:
                version_id = int(existing["current_version_id"])
            else:
                version_id = self._insert_version(run_id, record_id, record)

        self.connection.execute(
            """
            UPDATE records
            SET child = ?, source_url = ?, last_seen_run_id = ?,
                current_version_id = ?, status = 'active', missing_count = 0
            WHERE id = ?
            """,
            (record.child, record.source_url, run_id, version_id, record_id),
        )
        self.connection.execute(
            "INSERT INTO run_observations(run_id, record_id, version_id) "
            "VALUES (?, ?, ?)",
            (run_id, record_id, version_id),
        )
        self.connection.execute(
            """
            INSERT INTO changes(
                run_id, record_id, change_type,
                previous_version_id, current_version_id
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (run_id, record_id, change_type, previous_version_id, version_id),
        )
        return change_type

    @staticmethod
    def _date_value(value: Any) -> date | None:
        if not isinstance(value, str) or len(value) < 10:
            return None
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None

    def _record_is_covered(
        self, row: sqlite3.Row, coverage: dict[str, Any]
    ) -> bool:
        kind = coverage.get("kind", "full")
        if kind == "full":
            return True
        if kind in {"excluded_by_policy", "current_state"}:
            return False
        if kind == "page_window":
            # A record can leave a recent page simply because newer records arrived.
            # Absence is therefore never authoritative for page-bounded sources.
            return False
        if kind == "record_keys":
            return row["source_key"] in set(coverage.get("source_keys") or [])
        if kind != "date_window":
            raise ValueError(f"Unsupported source coverage kind: {kind}")

        start = self._date_value(coverage.get("start"))
        end = self._date_value(coverage.get("end"))
        if start is None or end is None or start >= end:
            raise ValueError("Date-window coverage requires start < end")
        payload = json.loads(row["payload_json"])
        for item in payload.get("dates") or []:
            if not isinstance(item, dict):
                continue
            item_start = self._date_value(item.get("start") or item.get("value"))
            item_end = self._date_value(item.get("end")) or item_start
            if item_start is None or item_end is None:
                continue
            if item_start < end and item_end >= start:
                return True
        return False

    def _mark_missing(self, run_id: int, check: SourceCheck) -> list[str]:
        unseen = self.connection.execute(
            """
            SELECT r.id, r.source_key, r.current_version_id, r.missing_count,
                   v.payload_json
            FROM records r
            JOIN record_versions v ON v.id = r.current_version_id
            WHERE student_id = ? AND source_type = ? AND last_seen_run_id != ?
              AND status != 'removed'
            """,
            (check.student_id, check.source_type, run_id),
        ).fetchall()
        changes: list[str] = []
        for row in unseen:
            if not self._record_is_covered(row, check.coverage):
                continue
            missing_count = int(row["missing_count"]) + 1
            change_type = "removed" if missing_count >= 2 else "missing"
            self.connection.execute(
                "UPDATE records SET missing_count = ?, status = ? WHERE id = ?",
                (missing_count, change_type, row["id"]),
            )
            self.connection.execute(
                """
                INSERT INTO changes(
                    run_id, record_id, change_type,
                    previous_version_id, current_version_id
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    row["id"],
                    change_type,
                    row["current_version_id"],
                    row["current_version_id"],
                ),
            )
            changes.append(change_type)
        return changes

    def store_checks(self, run_id: int, checks: Iterable[SourceCheck]) -> dict[str, Any]:
        change_counts: Counter[str] = Counter()
        source_summaries: list[dict[str, Any]] = []
        checks_list = list(checks)
        try:
            for check in checks_list:
                self.connection.execute(
                    """
                    INSERT INTO source_checks(
                        run_id, student_id, child, source_type, status,
                        complete, record_count, error, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        check.student_id,
                        check.child,
                        check.source_type,
                        check.status,
                        int(check.complete),
                        len(check.records),
                        check.error,
                        json.dumps(
                            {**check.metadata, "coverage": check.coverage},
                            ensure_ascii=False,
                        ),
                    ),
                )
                if check.error is None:
                    identities = [record.identity() for record in check.records]
                    if len(identities) != len(set(identities)):
                        raise ValueError(
                            f"Duplicate record identity in {check.child}/{check.source_type}"
                        )
                    for record in check.records:
                        change_counts[self._store_record(run_id, record)] += 1
                    if check.complete:
                        change_counts.update(self._mark_missing(run_id, check))
                source_summaries.append(
                    {
                        "child": check.child,
                        "source": check.source_type,
                        "status": check.status,
                        "complete": check.complete,
                        "records": len(check.records),
                        "error": check.error,
                        "metadata": check.metadata,
                        "coverage": check.coverage,
                    }
                )

            status = (
                "complete"
                if all(c.error is None and c.complete for c in checks_list)
                else "partial"
            )
            self.connection.execute(
                "UPDATE harvest_runs SET completed_at = ?, status = ? WHERE id = ?",
                (utc_now(), status, run_id),
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            self.connection.execute(
                "UPDATE harvest_runs SET completed_at = ?, status = 'failed' WHERE id = ?",
                (utc_now(), run_id),
            )
            self.connection.commit()
            raise

        return {
            "run_id": run_id,
            "status": status,
            "sources": source_summaries,
            "changes": dict(sorted(change_counts.items())),
        }

    def finalize_harvest_run(self, run_id: int, *, status: str) -> None:
        """Finalize after optional restricted-message processing has completed."""
        if status not in {"complete", "partial", "failed"}:
            raise ValueError("Invalid harvest run status")
        self.connection.execute(
            "UPDATE harvest_runs SET completed_at = ?, status = ? WHERE id = ?",
            (utc_now(), status, run_id),
        )
        self.connection.commit()

    def diff(
        self, run_id: int | None = None, *, include_noise: bool = False
    ) -> dict[str, Any]:
        if run_id is None:
            row = self.connection.execute(
                "SELECT id FROM harvest_runs WHERE status != 'running' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if row is None:
                raise ValueError("No completed harvest run exists")
            run_id = int(row["id"])
        query = """
            SELECT c.change_type, r.child, r.source_type, r.source_key,
                   r.source_url, v.title, v.content_hash
            FROM changes c
            JOIN records r ON r.id = c.record_id
            LEFT JOIN record_versions v ON v.id = c.current_version_id
            WHERE c.run_id = ?
        """
        params: list[Any] = [run_id]
        query += " ORDER BY r.child, r.source_type, r.source_key"
        rows = self.connection.execute(query, params).fetchall()
        items = [
            {
                "change": row["change_type"],
                "child": row["child"],
                "children": [row["child"]],
                "source": row["source_type"],
                "source_key": row["source_key"],
                "source_url": row["source_url"],
                "title": row["title"] or "",
                "_content_hash": row["content_hash"] or "",
            }
            for row in rows
        ]
        if not include_noise:
            items = [
                item
                for item in items
                if item["change"] not in {"unchanged", "raw_changed", "missing"}
                and not (
                    item["source"] == "frontpage" and item["change"] == "removed"
                )
            ]
            items = self._deduplicate_household_frontpage(items)
        for item in items:
            item.pop("_content_hash", None)
        counts = Counter(item["change"] for item in items)
        return {
            "run_id": run_id,
            "counts": dict(sorted(counts.items())),
            "items": items,
        }

    def latest_completed_run_id(self) -> int:
        row = self.connection.execute(
            "SELECT id FROM harvest_runs WHERE status = 'complete' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            raise ValueError("No completed harvest run exists")
        return int(row["id"])

    def message_thread_states(
        self, student_id: int | None = None
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM message_thread_state"
        params: tuple[Any, ...] = ()
        if student_id is not None:
            query += " WHERE student_id = ?"
            params = (student_id,)
        query += " ORDER BY child, inbox_position, thread_ref"
        rows = self.connection.execute(query, params).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            value = dict(row)
            value["participants"] = json.loads(
                value.pop("participant_display_json")
            )
            for field in (
                "exists_now",
                "server_unread",
                "is_new_thread",
                "has_new_message",
                "latest_unretrieved",
            ):
                value[field] = bool(value[field])
            result.append(value)
        return result

    def message_thread_states_by_ref(
        self, student_id: int
    ) -> dict[str, dict[str, Any]]:
        return {
            state["thread_ref"]: state
            for state in self.message_thread_states(student_id)
        }

    def replace_message_thread_states(
        self,
        *,
        student_id: int,
        child: str,
        states: Iterable[dict[str, Any]],
        complete: bool,
    ) -> None:
        states_list = list(states)
        now = utc_now()
        allowed = {
            "thread_ref",
            "student_id",
            "child",
            "participants",
            "exists_now",
            "server_unread",
            "is_new_thread",
            "has_new_message",
            "latest_unretrieved",
            "latest_activity_at",
            "inbox_position",
            "latest_message_ref",
            "latest_inbox_message_ref",
            "last_retrieved_message_ref",
            "policy_version",
        }
        seen: set[str] = set()
        try:
            for state in states_list:
                unknown = set(state) - allowed
                if unknown:
                    raise ValueError(
                        f"Restricted message state contains unknown fields: "
                        f"{sorted(unknown)}"
                    )
                if int(state["student_id"]) != student_id or state["child"] != child:
                    raise ValueError("Restricted message state child scope mismatch")
                thread_ref = str(state["thread_ref"])
                latest_message_ref = str(state["latest_message_ref"])
                latest_inbox_message_ref = state.get("latest_inbox_message_ref")
                if not re.fullmatch(r"thr_[a-z2-7]{16}", thread_ref):
                    raise ValueError("Invalid opaque thread reference")
                if not re.fullmatch(r"msg_[a-z2-7]{16}", latest_message_ref):
                    raise ValueError("Invalid opaque message reference")
                if latest_inbox_message_ref is not None and not re.fullmatch(
                    r"msg_[a-z2-7]{16}", str(latest_inbox_message_ref)
                ):
                    raise ValueError("Invalid opaque inbox message reference")
                participants = state.get("participants") or []
                if not isinstance(participants, list) or any(
                    not isinstance(name, str)
                    or not name
                    or len(name) > 100
                    or "\n" in name
                    or len(name.split()) > 2
                    for name in participants
                ):
                    raise ValueError("Invalid minimized participant display name")
                previous = self.connection.execute(
                    "SELECT last_retrieved_message_ref FROM message_thread_state "
                    "WHERE thread_ref = ?",
                    (thread_ref,),
                ).fetchone()
                last_retrieved = state.get("last_retrieved_message_ref")
                if previous is not None and previous["last_retrieved_message_ref"]:
                    last_retrieved = previous["last_retrieved_message_ref"]
                latest_activity_at = state.get("latest_activity_at")
                if latest_activity_at is not None:
                    try:
                        datetime.fromisoformat(str(latest_activity_at))
                    except ValueError as error:
                        raise ValueError("Invalid latest message chronology") from error
                inbox_position = int(state.get("inbox_position", 0))
                if inbox_position < 0:
                    raise ValueError("Invalid inbox position")
                latest_unretrieved = last_retrieved != latest_message_ref
                self.connection.execute(
                    """
                    INSERT INTO message_thread_state(
                        thread_ref, student_id, child, participant_display_json,
                        exists_now, server_unread, is_new_thread, has_new_message,
                        latest_unretrieved, latest_activity_at, inbox_position,
                        latest_message_ref, latest_inbox_message_ref,
                        last_retrieved_message_ref, observed_at, policy_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(thread_ref) DO UPDATE SET
                        student_id = excluded.student_id,
                        child = excluded.child,
                        participant_display_json = excluded.participant_display_json,
                        exists_now = excluded.exists_now,
                        server_unread = excluded.server_unread,
                        is_new_thread = excluded.is_new_thread,
                        has_new_message = excluded.has_new_message,
                        latest_unretrieved = excluded.latest_unretrieved,
                        latest_activity_at = excluded.latest_activity_at,
                        inbox_position = excluded.inbox_position,
                        latest_message_ref = excluded.latest_message_ref,
                        latest_inbox_message_ref = excluded.latest_inbox_message_ref,
                        last_retrieved_message_ref = excluded.last_retrieved_message_ref,
                        observed_at = excluded.observed_at,
                        policy_version = excluded.policy_version
                    """,
                    (
                        thread_ref,
                        student_id,
                        child,
                        json.dumps(participants, ensure_ascii=False),
                        int(bool(state.get("exists_now", True))),
                        int(bool(state.get("server_unread"))),
                        int(bool(state.get("is_new_thread"))),
                        int(bool(state.get("has_new_message"))),
                        int(latest_unretrieved),
                        latest_activity_at,
                        inbox_position,
                        latest_message_ref,
                        latest_inbox_message_ref,
                        last_retrieved,
                        now,
                        str(state["policy_version"]),
                    ),
                )
                if latest_inbox_message_ref is None:
                    self.connection.execute(
                        "DELETE FROM message_processing_attempts "
                        "WHERE thread_ref = ?",
                        (thread_ref,),
                    )
                else:
                    self.connection.execute(
                        "DELETE FROM message_processing_attempts "
                        "WHERE thread_ref = ? AND message_ref != ?",
                        (thread_ref, latest_inbox_message_ref),
                    )
                seen.add(thread_ref)
            if complete:
                if seen:
                    placeholders = ",".join("?" for _ in seen)
                    self.connection.execute(
                        f"DELETE FROM message_thread_state "
                        f"WHERE student_id = ? AND thread_ref NOT IN ({placeholders})",
                        (student_id, *sorted(seen)),
                    )
                else:
                    self.connection.execute(
                        "DELETE FROM message_thread_state WHERE student_id = ?",
                        (student_id,),
                    )
                self.connection.execute(
                    "DELETE FROM message_processing_attempts "
                    "WHERE thread_ref NOT IN "
                    "(SELECT thread_ref FROM message_thread_state)"
                )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def mark_message_retrieved(
        self, *, thread_ref: str, message_ref: str
    ) -> None:
        self.connection.execute(
            """
            UPDATE message_thread_state
            SET last_retrieved_message_ref = ?,
                latest_unretrieved = CASE
                    WHEN latest_message_ref = ? THEN 0 ELSE latest_unretrieved END
            WHERE thread_ref = ?
            """,
            (message_ref, message_ref, thread_ref),
        )
        self.connection.commit()

    @staticmethod
    def _deduplicate_household_frontpage(
        items: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
        result: list[dict[str, Any]] = []
        for item in items:
            if item["source"] != "frontpage" or item["change"] not in {
                "new",
                "content_changed",
            }:
                result.append(item)
                continue
            key = (
                item["change"],
                item["source_key"],
                item["_content_hash"],
            )
            existing = grouped.get(key)
            if existing is None:
                grouped[key] = item
                result.append(item)
                continue
            children = sorted(set(existing["children"] + item["children"]))
            existing["child"] = "household"
            existing["children"] = children
            source_urls = existing.setdefault("source_urls", [existing["source_url"]])
            if item["source_url"] not in source_urls:
                source_urls.append(item["source_url"])
        return result
