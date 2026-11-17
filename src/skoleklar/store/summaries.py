"""The day summaries, with the weekly-plan versions and lesson slots they read.

`SnapshotStore` combines this repository with the other layers. Summaries
remain tied to plan versions and must not be shown after their source changes.
"""
from __future__ import annotations

import json
import re
import sqlite3
from typing import Any, Iterable

from skoleklar.store.capture import utc_now

def weekly_plan_week(source_key: str) -> tuple[int, int] | None:
    """(ISO year, ISO week) of a weekly-plan key such as `class:40-2026`."""
    match = re.fullmatch(r"class:(\d{1,2})-(\d{4})", source_key or "")
    return (int(match.group(2)), int(match.group(1))) if match else None


class SummaryRepository:
    connection: sqlite3.Connection

    def weekly_plan_versions(
        self, *, run_id: int | None = None, weeks: Iterable[tuple[int, int]] = ()
    ) -> list[dict[str, Any]]:
        """Weekly plans observed in `run_id` (default: the latest run) or of `weeks`.

        `weeks` holds (ISO year, ISO week) pairs. Each plan appears once, with the
        version the run observed or, for a plan selected by week, its current one.
        A partial latest run still counts: its plans were observed.
        """
        if run_id is None:
            row = self.connection.execute(
                "SELECT id FROM harvest_runs WHERE completed_at IS NOT NULL "
                "AND status != 'failed' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            run_id = int(row["id"]) if row is not None else 0
        rows = self.connection.execute(
            """
            SELECT r.id AS record_id, r.child, r.source_key,
                   COALESCE(o.version_id, r.current_version_id) AS version_id
            FROM records r
            LEFT JOIN run_observations o ON o.record_id = r.id AND o.run_id = ?
            WHERE r.source_type = 'weekly_plan' AND r.status != 'removed'
            ORDER BY r.child, r.source_key
            """,
            (run_id,),
        ).fetchall()
        wanted = set(weeks)
        observed = {
            row["record_id"]
            for row in self.connection.execute(
                "SELECT record_id FROM run_observations WHERE run_id = ?", (run_id,)
            )
        }
        result = []
        for row in rows:
            week = weekly_plan_week(row["source_key"])
            if row["record_id"] not in observed and week not in wanted:
                continue
            version = self.connection.execute(
                "SELECT id, content_hash, payload_json FROM record_versions WHERE id = ?",
                (row["version_id"],),
            ).fetchone()
            if version is None:
                continue
            result.append(
                {
                    "record_id": int(row["record_id"]),
                    "version_id": int(version["id"]),
                    "child": row["child"],
                    "source_key": row["source_key"],
                    "week": week,
                    "content_hash": version["content_hash"],
                    "plan": json.loads(version["payload_json"]).get("source_data") or {},
                }
            )
        return result

    def day_summary_run(self, cache_key: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT id, status FROM day_summary_runs WHERE cache_key = ?", (cache_key,)
        ).fetchone()
        return dict(row) if row is not None else None

    def begin_day_summary_run(
        self,
        *,
        cache_key: str,
        record_id: int,
        version_id: int,
        child: str,
        content_hash: str,
        provider: str,
        model: str,
        manifest_id: str,
        manifest_hash: str,
    ) -> int:
        existing = self.day_summary_run(cache_key)
        if existing is not None:
            # A failed or interrupted attempt is retried under the same key.
            self.connection.execute(
                """
                UPDATE day_summary_runs
                SET started_at = ?, completed_at = NULL, status = 'running',
                    response_id = NULL, usage_json = '{}', error = NULL,
                    version_id = ?
                WHERE id = ? AND status != 'complete'
                """,
                (utc_now(), version_id, existing["id"]),
            )
            self.connection.commit()
            return int(existing["id"])
        cursor = self.connection.execute(
            """
            INSERT INTO day_summary_runs(
                cache_key, record_id, version_id, child, content_hash, provider,
                model, manifest_id, manifest_hash, started_at, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'running')
            """,
            (cache_key, record_id, version_id, child, content_hash, provider, model,
             manifest_id, manifest_hash, utc_now()),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def complete_day_summary_run(
        self,
        run_id: int,
        *,
        days: list[dict[str, Any]],
        response_id: str,
        usage: dict[str, Any],
    ) -> None:
        try:
            self.connection.execute("DELETE FROM day_summaries WHERE run_id = ?", (run_id,))
            self.connection.executemany(
                """
                INSERT INTO day_summaries(run_id, date, headline, text, general_used, fact_keys_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (run_id, day["date"], day["headline"], day["text"],
                     int(bool(day["general_used"])), json.dumps(day.get("fact_keys") or []))
                    for day in days
                ],
            )
            self.connection.execute(
                """
                UPDATE day_summary_runs
                SET completed_at = ?, status = 'complete', response_id = ?,
                    usage_json = ?, error = NULL
                WHERE id = ?
                """,
                (utc_now(), response_id, json.dumps(usage, sort_keys=True), run_id),
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def fail_day_summary_run(self, run_id: int, error: str) -> None:
        self.connection.execute(
            """
            UPDATE day_summary_runs
            SET completed_at = ?, status = 'failed', error = ?
            WHERE id = ?
            """,
            (utc_now(), error[:500], run_id),
        )
        self.connection.commit()

    def day_summary_days(self, child: str, year: int, week: int) -> dict[str, Any] | None:
        """The summaries for a child's plan of one ISO week, matching its current content.

        None when no plan for that week is stored. Otherwise `days` maps each date
        to its summary row and is None while no summary matches the current plan.
        """
        try:
            plans = self.connection.execute(
                """
                SELECT r.id, r.source_key, v.content_hash
                FROM records r JOIN record_versions v ON v.id = r.current_version_id
                WHERE r.source_type = 'weekly_plan' AND r.child = ? AND r.status != 'removed'
                """,
                (child,),
            ).fetchall()
            plan = next((row for row in plans
                         if weekly_plan_week(row["source_key"]) == (year, week)), None)
            if plan is None:
                return None
            run = self.connection.execute(
                """
                SELECT id FROM day_summary_runs
                WHERE record_id = ? AND content_hash = ? AND status = 'complete'
                ORDER BY completed_at DESC, id DESC LIMIT 1
                """,
                (plan["id"], plan["content_hash"]),
            ).fetchone()
        except sqlite3.OperationalError:
            # A read-only database from before day summaries has no such table.
            return {"days": None}
        if run is None:
            return {"days": None}
        rows = self.connection.execute(
            "SELECT date, headline, text, general_used FROM day_summaries WHERE run_id = ?",
            (run["id"],),
        ).fetchall()
        return {"days": {row["date"]: dict(row) for row in rows}}

    def plan_facts(self, version_id: int) -> list[dict[str, Any]]:
        """The current facts derived from one weekly-plan version: what its summary may retell."""
        rows = self.connection.execute(
            """
            SELECT DISTINCT f.stable_key, f.item_type, f.title, f.effective_date
            FROM v_current_facts f JOIN derived_item_sources s ON s.item_id = f.id
            WHERE s.version_id = ? AND f.effective_date IS NOT NULL
            ORDER BY f.effective_date, f.stable_key
            """,
            (version_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def retold_fact_keys(self) -> set[str]:
        """Stable keys of facts a current day summary's headline retells (day-summary-v3)."""
        try:
            rows = self.connection.execute(
                """
                SELECT d.fact_keys_json FROM day_summaries d
                JOIN day_summary_runs run ON run.id = d.run_id AND run.status = 'complete'
                JOIN records r ON r.id = run.record_id
                JOIN record_versions v ON v.id = r.current_version_id
                WHERE run.content_hash = v.content_hash AND d.headline IS NOT NULL
                """
            ).fetchall()
        except sqlite3.OperationalError:
            # A read-only database from before 0007 has no such column.
            return set()
        return {key for row in rows for key in json.loads(row[0] or "[]")}

    def lesson_slots(self, day: str) -> list[tuple[str, dict[str, Any]]]:
        """(child, calendar event) for each lesson slot the latest harvest saw on `day`.

        A slot the last harvest did not see (`missing`) is left out, so a moved
        lesson is not shown twice.
        """
        rows = self.connection.execute(
            """
            SELECT r.child, v.payload_json
            FROM records r JOIN record_versions v ON v.id = r.current_version_id
            WHERE r.source_type = 'calendar' AND r.status = 'active'
              AND r.source_key LIKE 'LessonsEvents:%'
              AND json_extract(v.payload_json, '$.source_data.event.start') LIKE ? || 'T%'
            """,
            (day,),
        ).fetchall()
        result = []
        for row in rows:
            event = (json.loads(row["payload_json"]).get("source_data") or {}).get("event")
            if isinstance(event, dict):
                result.append((row["child"], event))
        return result
