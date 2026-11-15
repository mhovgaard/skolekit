"""Layer 2: typed rows parsed in code from the current source versions.

Each scan rewrites these tables in one transaction after collection, from the
current version of every `active` or `missing` record (docs/data-foundation.md
§3–§6). A value the parser cannot read becomes NULL; a row the schema refuses
is left out and counted, so an odd source item never stops a scan.

What one form, event or notice holds is read by skolekit.items, the same
readers the live view uses; this module stores them, merges an event seen in
two feeds, and keeps a form's newest sighting. The school's wording reads the
items, so each refresh records the school's digest next to the parser's
version: a trace shows which rules read a row.
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from skolekit.items import read_event, read_form, read_notice
from skolekit.school import School

PARSER_VERSION = "typed-v1"
_TABLES = ("src_forms", "src_calendar_events", "src_calendar_event_levels",
           "src_notices", "src_notice_days")


def sync_children(connection: sqlite3.Connection, children: Iterable[Any]) -> None:
    """Upsert the profile's children (layer 0); a child is never deleted."""
    for child in children:
        if child.class_level is None or child.class_code is None:
            raise ValueError(f"No class level for {child.child}; set class_level in profile.toml")
        connection.execute(
            """
            INSERT INTO children(student_id, name, class_level, class_code)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(student_id) DO UPDATE SET
                name = excluded.name, class_level = excluded.class_level,
                class_code = excluded.class_code
            """,
            (child.student_id, child.child, child.class_level, child.class_code))


def _current(connection: sqlite3.Connection, source_type: str) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT r.id, r.student_id, r.source_key, r.source_url, r.last_seen_run_id,
               v.id AS version_id, v.title, v.body_text, v.payload_json
        FROM records r
        JOIN record_versions v ON v.id = r.current_version_id
        JOIN children c ON c.student_id = r.student_id
        WHERE r.source_type = ? AND r.status IN ('active', 'missing')
        ORDER BY r.last_seen_run_id DESC, r.id DESC
        """,
        (source_type,)).fetchall()


class _Writer:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection
        self.counts = dict.fromkeys(_TABLES, 0)
        self.rejected = 0

    def insert(self, table: str, row: dict[str, Any]) -> bool:
        columns = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        try:
            self.connection.execute("SAVEPOINT src_row")
            self.connection.execute(f"INSERT INTO {table}({columns}) VALUES ({marks})",
                                    tuple(row.values()))
            self.connection.execute("RELEASE src_row")
        except sqlite3.IntegrityError:
            self.connection.execute("ROLLBACK TO src_row")
            self.connection.execute("RELEASE src_row")
            self.rejected += 1
            return False
        self.counts[table] += 1
        return True


def _forms(connection: sqlite3.Connection, writer: _Writer, school: School) -> None:
    seen: set[tuple[int, str]] = set()
    for record in _current(connection, "forms"):
        form = read_form(json.loads(record["payload_json"]), source_key=record["source_key"],
                         source_url=record["source_url"], title=record["title"],
                         wording=school.wording)
        # Newest sighting first: a form's earlier link-keyed record yields to it.
        if form is None:
            writer.rejected += 1
            continue
        if (record["student_id"], form["form_key"]) in seen:
            continue
        seen.add((record["student_id"], form["form_key"]))
        writer.insert("src_forms", {
            "student_id": record["student_id"],
            "form_key": form["form_key"],
            "version_id": record["version_id"],
            "type_label": form["type_label"],
            "title": form["title"],
            "list_state": form["list_state"],
            "status_text": form["status_text"],
            "booked_local": form["booked_local"],
            "answered_on": form["answered_on"],
            "deadline": form["deadline"],
        })


def _calendar(connection: sqlite3.Connection, writer: _Writer) -> None:
    # One row per event: an event in both feeds is merged, its newest version wins.
    events: dict[tuple[int, int], dict[str, Any]] = {}
    for record in _current(connection, "calendar"):
        event = read_event(json.loads(record["payload_json"]), source_key=record["source_key"],
                           title=record["title"], body_text=record["body_text"])
        if event is None:
            continue
        if event["event_id"] is None:
            writer.rejected += 1
            continue
        row = events.setdefault((record["student_id"], event["event_id"]), {
            "newest_version": 0, "levels": set(), "SchoolEvents": None, "ActivitiesEvents": None})
        row[event["feed"]] = record["version_id"]
        row["levels"] |= set(event["class_levels"])
        if record["version_id"] > row["newest_version"]:
            row.update(newest_version=record["version_id"], title=event["title"],
                       description=event["description"], starts=event["starts_local"],
                       ends=event["ends_local"], all_day=int(event["all_day"]))
    for (student_id, event_id), row in sorted(events.items()):
        if row["starts"] is None:
            writer.rejected += 1
            continue
        if writer.insert("src_calendar_events", {
            "student_id": student_id, "event_id": event_id, "title": row["title"],
            "description": row["description"], "starts_local": row["starts"],
            "ends_local": row["ends"], "all_day": row["all_day"],
            "school_version_id": row["SchoolEvents"],
            "activities_version_id": row["ActivitiesEvents"],
        }):
            for level in sorted(row["levels"]):
                writer.insert("src_calendar_event_levels", {
                    "student_id": student_id, "event_id": event_id, "class_level": level})


def _notices(connection: sqlite3.Connection, writer: _Writer, school: School) -> None:
    for record in _current(connection, "frontpage"):
        notice = read_notice(record["body_text"], title=record["title"], wording=school.wording)
        inserted = writer.insert("src_notices", {
            "student_id": record["student_id"],
            "notice_key": record["source_key"],
            "version_id": record["version_id"],
            "title": notice["title"],
            "signup_from": notice["signup_from"],
            "signup_until": notice["signup_until"],
            "expires_on": notice["expires_on"],
            "minutes": notice["minutes"],
        })
        if inserted:
            for day in notice["days"]:
                writer.insert("src_notice_days", {
                    "student_id": record["student_id"], "notice_key": record["source_key"],
                    "day": day})


def parser_version(school: School) -> str:
    """`typed-v1 school:74c680438638`: the parser and the school's rules that read a refresh."""
    return f"{PARSER_VERSION} school:{school.digest()}"


def rebuild(connection: sqlite3.Connection, *, run_id: int, children: Iterable[Any],
            school: School) -> dict[str, Any]:
    """Rewrite layer 2 from the current record versions; one transaction."""
    version = parser_version(school)
    if not connection.in_transaction:
        connection.execute("BEGIN IMMEDIATE")
    try:
        sync_children(connection, children)
        for table in reversed(_TABLES):
            connection.execute(f"DELETE FROM {table}")
        writer = _Writer(connection)
        _forms(connection, writer, school)
        _calendar(connection, writer)
        _notices(connection, writer, school)
        connection.execute(
            """
            INSERT INTO src_refreshes(run_id, parser_version, refreshed_at, row_counts, rejected_count)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET parser_version = excluded.parser_version,
                refreshed_at = excluded.refreshed_at, row_counts = excluded.row_counts,
                rejected_count = excluded.rejected_count
            """,
            (run_id, version, datetime.now(UTC).replace(microsecond=0).isoformat()
             .replace("+00:00", "Z"), json.dumps(writer.counts, sort_keys=True), writer.rejected))
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return {"parser_version": version, "rows": writer.counts, "rejected": writer.rejected}
