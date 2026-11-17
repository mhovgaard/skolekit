"""Layer 3: the derivations and their facts, the archive stage's decisions, the
consolidation, and the reports on them. `current_derived_items` reads v_live_facts.

`SnapshotStore` combines this repository with the other layers. Keep
source/member provenance and stored prompt identities intact.
"""
from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from datetime import date
from typing import Any

from skoleklar.migrations import SchemaError, current_version
from skoleklar.store.capture import utc_now

DANISH_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "maj": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "okt": 10,
    "nov": 11,
    "dec": 12,
}


def source_date_from_payload(payload: dict[str, Any]) -> date | None:
    values: list[str] = []
    message = (payload.get("source_data") or {}).get("message") or {}
    if message.get("sent_received_date_text"):
        values.append(str(message["sent_received_date_text"]))
    for value in payload.get("dates") or []:
        if isinstance(value, dict) and value.get("value"):
            values.append(str(value["value"]))
    for value in values:
        parsed = _parse_source_date(value)
        if parsed is not None:
            return parsed
    return None


def _parse_source_date(value: str) -> date | None:
    iso_match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", value)
    if iso_match:
        try:
            return date.fromisoformat(iso_match.group(1))
        except ValueError:
            pass
    danish_match = re.search(
        r"\b(\d{1,2})\.\s*([a-zæøå]{3,})\.?\s+(\d{4})\b",
        value.casefold(),
    )
    if not danish_match:
        return None
    month = DANISH_MONTHS.get(danish_match.group(2)[:3])
    if month is None:
        return None
    try:
        return date(int(danish_match.group(3)), month, int(danish_match.group(1)))
    except ValueError:
        return None


# What a derived item is (derive-v10): read from its payload, carried by consolidation.
ITEM_SHAPE = ("category", "moves_time", "recurring")


class FactRepository:
    connection: sqlite3.Connection

    def derivation_candidates(
        self,
        run_id: int | None = None,
        *,
        source_types: list[str] | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        run_id = run_id or self.latest_completed_run_id()
        query = """
            SELECT c.change_type, r.id AS record_id, r.child, r.source_type,
                   r.source_key, r.source_url, v.id AS version_id,
                   v.content_hash, v.payload_json
            FROM changes c
            JOIN records r ON r.id = c.record_id
            JOIN record_versions v ON v.id = c.current_version_id
            WHERE c.run_id = ?
              AND c.change_type IN ('new', 'content_changed', 'state_changed')
        """
        params: list[Any] = [run_id]
        if source_types:
            placeholders = ",".join("?" for _ in source_types)
            query += f" AND r.source_type IN ({placeholders})"
            params.extend(source_types)
        query += " ORDER BY r.child, r.source_type, r.source_key"
        rows = self.connection.execute(query, params).fetchall()
        grouped: dict[tuple[Any, ...], dict[str, Any]] = {}
        candidates: list[dict[str, Any]] = []
        for row in rows:
            if row["source_type"] == "frontpage":
                group_key = (
                    row["source_type"],
                    row["source_key"],
                    row["content_hash"],
                    row["change_type"],
                )
            else:
                group_key = ("record", row["record_id"], row["version_id"])
            candidate = grouped.get(group_key)
            source = {
                "record_id": int(row["record_id"]),
                "version_id": int(row["version_id"]),
                "child": row["child"],
                "source_url": row["source_url"],
                "payload_json": row["payload_json"],
            }
            if candidate is None:
                candidate = {
                    "harvest_run_id": run_id,
                    "change": row["change_type"],
                    "source_type": row["source_type"],
                    "source_key": row["source_key"],
                    "content_hash": row["content_hash"],
                    "sources": [],
                }
                grouped[group_key] = candidate
                candidates.append(candidate)
            candidate["sources"].append(source)
        return candidates[:limit] if limit is not None else candidates

    def backfill_candidates(
        self,
        *,
        since: date,
        source_types: list[str],
        limit: int,
    ) -> list[dict[str, Any]]:
        if not source_types:
            raise ValueError("Backfill requires at least one source type")
        if limit < 1 or limit > 200:
            raise ValueError("Backfill limit must be between 1 and 200")
        placeholders = ",".join("?" for _ in source_types)
        rows = self.connection.execute(
            f"""
            SELECT r.id AS record_id, r.child, r.source_type, r.source_key,
                   r.source_url, v.id AS version_id, v.content_hash,
                   v.payload_json
            FROM records r
            JOIN record_versions v ON v.id = r.current_version_id
            WHERE r.status = 'active'
              AND r.source_type IN ({placeholders})
            """,
            source_types,
        ).fetchall()
        eligible: list[tuple[date, sqlite3.Row, dict[str, Any]]] = []
        for row in rows:
            payload = json.loads(row["payload_json"])
            source_date = source_date_from_payload(payload)
            if source_date is None or source_date < since:
                continue
            eligible.append((source_date, row, payload))
        eligible.sort(
            key=lambda value: (value[0], int(value[1]["version_id"])), reverse=True
        )

        run_id = self.latest_completed_run_id()
        grouped: dict[tuple[Any, ...], dict[str, Any]] = {}
        candidates: list[dict[str, Any]] = []
        for source_date, row, _payload in eligible:
            if row["source_type"] == "frontpage":
                group_key = (
                    row["source_type"],
                    row["source_key"],
                    row["content_hash"],
                )
            else:
                group_key = ("record", row["record_id"], row["version_id"])
            candidate = grouped.get(group_key)
            source = {
                "record_id": int(row["record_id"]),
                "version_id": int(row["version_id"]),
                "child": row["child"],
                "source_url": row["source_url"],
                "payload_json": row["payload_json"],
            }
            if candidate is None:
                candidate = {
                    "harvest_run_id": run_id,
                    "change": "backfill",
                    "source_type": row["source_type"],
                    "source_key": row["source_key"],
                    "content_hash": row["content_hash"],
                    "source_date": source_date.isoformat(),
                    "sources": [],
                }
                grouped[group_key] = candidate
                candidates.append(candidate)
            candidate["sources"].append(source)
        return candidates[:limit]

    def cached_derivation(self, cache_key: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT id, status, purpose FROM derivations WHERE cache_key = ?",
            (cache_key,),
        ).fetchone()
        return dict(row) if row is not None else None

    def activate_derivation(self, derivation_id: int) -> None:
        self.connection.execute(
            "UPDATE derivations SET purpose = 'live' WHERE id = ?", (derivation_id,)
        )
        self.connection.commit()

    def begin_derivation(
        self,
        *,
        harvest_run_id: int,
        cache_key: str,
        provider: str,
        model: str,
        prompt_version: str,
        taxonomy_version: str,
        purpose: str = "live",
    ) -> int:
        if purpose not in {"live", "validation"}:
            raise ValueError("Derivation purpose must be live or validation")
        cursor = self.connection.execute(
            """
            INSERT INTO derivations(
                harvest_run_id, cache_key, provider, model,
                prompt_version, taxonomy_version, purpose, started_at, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'running')
            """,
            (
                harvest_run_id,
                cache_key,
                provider,
                model,
                prompt_version,
                taxonomy_version,
                purpose,
                utc_now(),
            ),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def fail_derivation(self, derivation_id: int, error: str) -> None:
        self.connection.execute(
            """
            UPDATE derivations
            SET completed_at = ?, status = 'failed', error = ?
            WHERE id = ?
            """,
            (utc_now(), error[:2000], derivation_id),
        )
        self.connection.commit()

    def restart_derivation(self, derivation_id: int) -> None:
        self.connection.execute(
            """
            UPDATE derivations
            SET started_at = ?, completed_at = NULL, status = 'running',
                response_id = NULL, usage_json = '{}', error = NULL
            WHERE id = ? AND status = 'failed'
            """,
            (utc_now(), derivation_id),
        )
        self.connection.commit()

    def complete_derivation(
        self,
        derivation_id: int,
        *,
        items: list[dict[str, Any]],
        sources: list[dict[str, Any]],
        response_id: str,
        usage: dict[str, Any],
    ) -> list[int]:
        now = utc_now()
        item_ids: list[int] = []
        try:
            for source in sources:
                self.connection.execute(
                    """
                    INSERT OR IGNORE INTO derivation_sources(
                        derivation_id, record_id, version_id
                    ) VALUES (?, ?, ?)
                    """,
                    (derivation_id, source["record_id"], source["version_id"]),
                )
            for index, item in enumerate(items):
                cursor = self.connection.execute(
                    """
                    INSERT INTO derived_items(
                        derivation_id, item_index, stable_key, stream, item_type,
                        audience, child, title, summary, effective_date, end_date,
                        all_day, timing_rule, actionability, urgency,
                        urgency_reason, confidence, needs_review, review_reason,
                        evidence_json, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        derivation_id,
                        index,
                        item["stable_key"],
                        item["stream"],
                        item["type"],
                        item["audience"],
                        item["child"],
                        item["title"],
                        item["summary"],
                        item["effective_date"],
                        item["end_date"],
                        int(item["all_day"]),
                        item["timing_rule"],
                        item["actionability"],
                        item["urgency"],
                        item["urgency_reason"],
                        float(item["confidence"]),
                        int(item["needs_review"]),
                        item["review_reason"],
                        json.dumps(item["evidence"], ensure_ascii=False),
                        json.dumps(item, ensure_ascii=False, separators=(",", ":")),
                    ),
                )
                item_id = int(cursor.lastrowid)
                item_ids.append(item_id)
                for source in sources:
                    self.connection.execute(
                        """
                        INSERT INTO derived_item_sources(item_id, record_id, version_id)
                        VALUES (?, ?, ?)
                        """,
                        (item_id, source["record_id"], source["version_id"]),
                    )
                self.connection.execute(
                    """
                    INSERT INTO item_workflow(stable_key, first_seen_at, last_seen_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(stable_key) DO UPDATE SET last_seen_at = excluded.last_seen_at
                    """,
                    (item["stable_key"], now, now),
                )
            self.connection.execute(
                """
                UPDATE derivations
                SET completed_at = ?, status = 'complete', response_id = ?,
                    usage_json = ?, error = NULL
                WHERE id = ?
                """,
                (
                    now,
                    response_id,
                    json.dumps(usage, ensure_ascii=False, separators=(",", ":")),
                    derivation_id,
                ),
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return item_ids

    def current_derived_items(
        self, *, include_archived: bool = False
    ) -> list[dict[str, Any]]:
        # The view holds the rule for which facts are current (migration 0004).
        view = "v_live_facts" if include_archived else "v_current_facts"
        if current_version(self.connection) < 4:
            raise SchemaError(
                f"The database is at schema version {current_version(self.connection)}; "
                "open it once with a writing command to migrate it")
        rows = self.connection.execute(
            f"SELECT * FROM {view} ORDER BY effective_date, child, stream, id"
        ).fetchall()
        source_urls: dict[int, list[str]] = {}
        source_types: dict[int, list[str]] = {}
        item_ids = [int(row["id"]) for row in rows]
        if item_ids:
            placeholders = ",".join("?" for _ in item_ids)
            source_rows = self.connection.execute(
                f"""
                SELECT s.item_id, r.source_url, r.source_type
                FROM derived_item_sources s
                JOIN records r ON r.id = s.record_id
                WHERE s.item_id IN ({placeholders})
                ORDER BY s.item_id, r.source_url
                """,
                item_ids,
            ).fetchall()
            for source_row in source_rows:
                urls = source_urls.setdefault(int(source_row["item_id"]), [])
                if source_row["source_url"] not in urls:
                    urls.append(source_row["source_url"])
                types = source_types.setdefault(int(source_row["item_id"]), [])
                if source_row["source_type"] not in types:
                    types.append(source_row["source_type"])
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["all_day"] = bool(item["all_day"])
            item["needs_review"] = bool(item["needs_review"])
            item["evidence"] = json.loads(item.pop("evidence_json"))
            # derive-v10 says what an item is; facts derived before keep no such keys.
            payload = json.loads(item.pop("payload_json") or "{}")
            item.update({key: payload[key] for key in ITEM_SHAPE if key in payload})
            item["source_urls"] = source_urls.get(int(item["id"]), [])
            item["source_types"] = source_types.get(int(item["id"]), [])
            result.append(item)
        return result

    def apply_archival(
        self,
        *,
        as_of: date,
        policy_version: str,
        decisions: list[dict[str, Any]],
        usage: dict[str, Any] | None = None,
    ) -> int:
        stable_keys = [str(item["stable_key"]) for item in decisions]
        if len(stable_keys) != len(set(stable_keys)):
            raise ValueError("Archival decisions must have unique stable keys")
        for item in decisions:
            if item.get("decision") not in {"archive", "retain"}:
                raise ValueError(f"Invalid archival decision: {item.get('decision')}")
            if item.get("method") not in {"policy", "agent"}:
                raise ValueError(f"Invalid archival method: {item.get('method')}")
            if not str(item.get("reason") or "").strip():
                raise ValueError("Every archival decision requires a reason")

        now = utc_now()
        archived_count = sum(item["decision"] == "archive" for item in decisions)
        agent_review_count = sum(item["method"] == "agent" for item in decisions)
        try:
            cursor = self.connection.execute(
                """
                INSERT INTO archival_runs(
                    as_of, policy_version, started_at, completed_at, status,
                    candidate_count, archived_count, retained_count,
                    agent_review_count, usage_json
                ) VALUES (?, ?, ?, ?, 'complete', ?, ?, ?, ?, ?)
                """,
                (
                    as_of.isoformat(),
                    policy_version,
                    now,
                    now,
                    len(decisions),
                    archived_count,
                    len(decisions) - archived_count,
                    agent_review_count,
                    json.dumps(usage or {}, ensure_ascii=False, separators=(",", ":")),
                ),
            )
            run_id = int(cursor.lastrowid)
            for item in decisions:
                item_id = int(item["item_id"])
                stable_key = str(item["stable_key"])
                exists = self.connection.execute(
                    "SELECT 1 FROM derived_items WHERE id = ? AND stable_key = ?",
                    (item_id, stable_key),
                ).fetchone()
                if exists is None:
                    raise ValueError(
                        f"Archival item/stable-key mismatch: {item_id}/{stable_key}"
                    )
                self.connection.execute(
                    """
                    INSERT INTO archival_decisions(
                        run_id, item_id, stable_key, decision, reason, method
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        item_id,
                        stable_key,
                        item["decision"],
                        str(item["reason"])[:240],
                        item["method"],
                    ),
                )
                in_workflow = self.connection.execute(
                    "SELECT 1 FROM item_workflow WHERE stable_key = ?", (stable_key,)
                ).fetchone() is not None
                if item["decision"] == "archive":
                    archived = self.connection.execute(
                        "SELECT archived_at FROM item_archive WHERE stable_key = ?", (stable_key,)
                    ).fetchone()
                    if not in_workflow or (archived is not None and archived[0] is not None):
                        raise ValueError(
                            f"Item is missing from workflow or already archived: {stable_key}"
                        )
                    self.connection.execute(
                        """
                        INSERT INTO item_archive(stable_key, archived_at, archive_reason, archive_run_id)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(stable_key) DO UPDATE SET archived_at = excluded.archived_at,
                            archive_reason = excluded.archive_reason,
                            archive_run_id = excluded.archive_run_id
                        """,
                        (stable_key, now, str(item["reason"])[:240], run_id),
                    )
                elif item["method"] == "agent" and in_workflow:
                    self.connection.execute(
                        """
                        INSERT INTO item_archive(stable_key, reviewed_at, review_as_of, review_reason)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(stable_key) DO UPDATE SET reviewed_at = excluded.reviewed_at,
                            review_as_of = excluded.review_as_of,
                            review_reason = excluded.review_reason
                        """,
                        (stable_key, now, as_of.isoformat(), str(item["reason"])[:240]),
                    )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return run_id

    def archival_report(self, run_id: int | None = None) -> dict[str, Any] | None:
        if run_id is None:
            run = self.connection.execute(
                "SELECT * FROM archival_runs WHERE status = 'complete' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        else:
            run = self.connection.execute(
                "SELECT * FROM archival_runs WHERE id = ? AND status = 'complete'",
                (run_id,),
            ).fetchone()
        if run is None:
            return None
        rows = self.connection.execute(
            """
            SELECT decision.*, item.item_type, item.child, item.title,
                   item.effective_date, item.end_date
            FROM archival_decisions decision
            JOIN derived_items item ON item.id = decision.item_id
            WHERE decision.run_id = ?
            ORDER BY decision.decision, item.end_date, item.effective_date, item.id
            """,
            (run["id"],),
        ).fetchall()
        return {
            "archival_run_id": int(run["id"]),
            "as_of": run["as_of"],
            "policy_version": run["policy_version"],
            "completed_at": run["completed_at"],
            "candidate_count": int(run["candidate_count"]),
            "archived_count": int(run["archived_count"]),
            "retained_count": int(run["retained_count"]),
            "agent_review_count": int(run["agent_review_count"]),
            "usage": json.loads(run["usage_json"]),
            "decisions": [dict(row) for row in rows],
        }

    def cached_consolidation(self, cache_key: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT id, status FROM consolidations WHERE cache_key = ?",
            (cache_key,),
        ).fetchone()
        return dict(row) if row is not None else None

    def begin_consolidation(
        self,
        *,
        harvest_run_id: int,
        cache_key: str,
        provider: str,
        model: str,
        prompt_version: str,
        taxonomy_version: str,
        manifest_hash: str,
        input_count: int,
    ) -> int:
        cursor = self.connection.execute(
            """
            INSERT INTO consolidations(
                harvest_run_id, cache_key, provider, model, prompt_version,
                taxonomy_version, manifest_hash, input_count, started_at, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'running')
            """,
            (
                harvest_run_id,
                cache_key,
                provider,
                model,
                prompt_version,
                taxonomy_version,
                manifest_hash,
                input_count,
                utc_now(),
            ),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def restart_consolidation(self, consolidation_id: int) -> None:
        self.connection.execute(
            """
            UPDATE consolidations
            SET started_at = ?, completed_at = NULL, status = 'running',
                response_id = NULL, usage_json = '{}', error = NULL
            WHERE id = ? AND status = 'failed'
            """,
            (utc_now(), consolidation_id),
        )
        self.connection.commit()

    def fail_consolidation(self, consolidation_id: int, error: str) -> None:
        self.connection.execute(
            """
            UPDATE consolidations
            SET completed_at = ?, status = 'failed', error = ?
            WHERE id = ?
            """,
            (utc_now(), error[:2000], consolidation_id),
        )
        self.connection.commit()

    def complete_consolidation(
        self,
        consolidation_id: int,
        *,
        items: list[dict[str, Any]],
        response_id: str,
        usage: dict[str, Any],
    ) -> list[int]:
        item_ids: list[int] = []
        try:
            for index, item in enumerate(items):
                cursor = self.connection.execute(
                    """
                    INSERT INTO consolidated_items(
                        consolidation_id, item_index, stable_key, resolution,
                        stream, item_type, audience, child, title, summary,
                        effective_date, end_date, all_day, timing_rule,
                        actionability, urgency, urgency_reason, confidence,
                        needs_review, review_reason, reason, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        consolidation_id,
                        index,
                        item["stable_key"],
                        item["resolution"],
                        item["stream"],
                        item["item_type"],
                        item["audience"],
                        item["child"],
                        item["title"],
                        item["summary"],
                        item["effective_date"],
                        item["end_date"],
                        int(item["all_day"]),
                        item["timing_rule"],
                        item["actionability"],
                        item["urgency"],
                        item["urgency_reason"],
                        float(item["confidence"]),
                        int(item["needs_review"]),
                        item["review_reason"],
                        item["reason"],
                        json.dumps(item, ensure_ascii=False, separators=(",", ":")),
                    ),
                )
                consolidated_item_id = int(cursor.lastrowid)
                item_ids.append(consolidated_item_id)
                for derived_item_id in item["member_item_ids"]:
                    self.connection.execute(
                        """
                        INSERT INTO consolidation_members(
                            consolidated_item_id, derived_item_id
                        ) VALUES (?, ?)
                        """,
                        (consolidated_item_id, derived_item_id),
                    )
            self.connection.execute(
                """
                UPDATE consolidations
                SET completed_at = ?, status = 'complete', response_id = ?,
                    usage_json = ?, error = NULL
                WHERE id = ?
                """,
                (
                    utc_now(),
                    response_id,
                    json.dumps(usage, ensure_ascii=False, separators=(",", ":")),
                    consolidation_id,
                ),
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return item_ids

    def consolidation_report(
        self, consolidation_id: int | None = None
    ) -> dict[str, Any] | None:
        if consolidation_id is None:
            row = self.connection.execute(
                """
                SELECT * FROM consolidations
                WHERE status = 'complete'
                ORDER BY id DESC LIMIT 1
                """
            ).fetchone()
        else:
            row = self.connection.execute(
                "SELECT * FROM consolidations WHERE id = ? AND status = 'complete'",
                (consolidation_id,),
            ).fetchone()
        if row is None:
            return None
        item_rows = self.connection.execute(
            """
            SELECT * FROM consolidated_items
            WHERE consolidation_id = ?
            ORDER BY item_index
            """,
            (row["id"],),
        ).fetchall()
        items: list[dict[str, Any]] = []
        for item_row in item_rows:
            item = json.loads(item_row["payload_json"])
            item["id"] = int(item_row["id"])
            item["all_day"] = bool(item["all_day"])
            item["needs_review"] = bool(item["needs_review"])
            items.append(item)
        merged_groups = sum(item["resolution"] != "unchanged" for item in items)
        return {
            "consolidation_id": int(row["id"]),
            "harvest_run_id": int(row["harvest_run_id"]),
            "completed_at": row["completed_at"],
            "provider": row["provider"],
            "model": row["model"],
            "prompt_version": row["prompt_version"],
            "taxonomy_version": row["taxonomy_version"],
            "manifest_hash": row["manifest_hash"],
            "input_count": int(row["input_count"]),
            "output_count": len(items),
            "reduction_count": int(row["input_count"]) - len(items),
            "merged_group_count": merged_groups,
            "review_count": sum(bool(item["needs_review"]) for item in items),
            "usage": json.loads(row["usage_json"]),
            "items": items,
        }

    def derivation_report(
        self,
        *,
        prompt_version: str,
        model: str,
        source_types: list[str] | None = None,
        since: date | None = None,
        include_source_preview: bool = False,
    ) -> dict[str, Any]:
        query = """
            SELECT d.id AS derivation_id, d.completed_at, d.model, d.purpose,
                   r.id AS record_id, r.child, r.source_type, r.source_key,
                   r.source_url, v.id AS version_id, v.title, v.body_text,
                   v.payload_json
            FROM derivations d
            JOIN derivation_sources s ON s.derivation_id = d.id
            JOIN records r ON r.id = s.record_id
            JOIN record_versions v ON v.id = s.version_id
            WHERE d.status = 'complete'
              AND d.prompt_version = ?
              AND d.model = ?
              AND r.status = 'active'
              AND r.current_version_id = s.version_id
        """
        params: list[Any] = [prompt_version, model]
        if source_types:
            placeholders = ",".join("?" for _ in source_types)
            query += f" AND r.source_type IN ({placeholders})"
            params.extend(source_types)
        query += " ORDER BY d.id, r.child, r.id"
        rows = self.connection.execute(query, params).fetchall()

        documents: dict[int, dict[str, Any]] = {}
        for row in rows:
            payload = json.loads(row["payload_json"])
            source_date = source_date_from_payload(payload)
            if since is not None and (source_date is None or source_date < since):
                continue
            document = documents.setdefault(
                int(row["derivation_id"]),
                {
                    "derivation_id": int(row["derivation_id"]),
                    "completed_at": row["completed_at"],
                    "model": row["model"],
                    "purpose": row["purpose"],
                    "source": row["source_type"],
                    "source_key": row["source_key"],
                    "source_date": source_date.isoformat() if source_date else None,
                    "title": row["title"],
                    "sender": (
                        ((payload.get("source_data") or {}).get("message") or {}).get(
                            "sender"
                        )
                    ),
                    "children": [],
                    "source_urls": [],
                    "items": [],
                },
            )
            if row["child"] not in document["children"]:
                document["children"].append(row["child"])
            if row["source_url"] not in document["source_urls"]:
                document["source_urls"].append(row["source_url"])
            if include_source_preview:
                document["source_preview"] = re.sub(
                    r"\s+", " ", row["body_text"]
                ).strip()[:320]

        derivation_ids = list(documents)
        if derivation_ids:
            placeholders = ",".join("?" for _ in derivation_ids)
            item_rows = self.connection.execute(
                f"""
                SELECT id, derivation_id, stream, item_type, audience, child,
                       title, summary, effective_date, end_date, all_day, timing_rule,
                       actionability, urgency, urgency_reason, confidence,
                       needs_review, review_reason
                FROM derived_items
                WHERE derivation_id IN ({placeholders})
                ORDER BY derivation_id, item_index
                """,
                derivation_ids,
            ).fetchall()
            for row in item_rows:
                item = dict(row)
                item["all_day"] = bool(item["all_day"])
                item["needs_review"] = bool(item["needs_review"])
                documents[int(row["derivation_id"])]["items"].append(item)

        values = list(documents.values())
        item_types = Counter(
            item["item_type"] for document in values for item in document["items"]
        )
        urgency = Counter(
            item["urgency"] for document in values for item in document["items"]
        )
        review_count = sum(
            bool(item["needs_review"])
            for document in values
            for item in document["items"]
        )
        return {
            "prompt_version": prompt_version,
            "model": model,
            "since": since.isoformat() if since else None,
            "documents_processed": len(values),
            "documents_with_items": sum(bool(value["items"]) for value in values),
            "zero_item_documents": sum(not value["items"] for value in values),
            "item_count": sum(len(value["items"]) for value in values),
            "review_count": review_count,
            "item_types": dict(sorted(item_types.items())),
            "urgency": dict(sorted(urgency.items())),
            "documents": values,
        }
