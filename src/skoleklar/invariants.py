"""Checks every scan must pass before its database is uploaded.

docs/data-foundation.md, "Invariants". Each check is a query that returns the offending
rows; a check passes when it returns none. A failure stops the upload, so the
bucket keeps the previous scan's file.
"""
from __future__ import annotations

import sqlite3

from skoleklar.migrations import SchemaError, check_versions, current_version, latest_version

_CURRENT_VERSION = """
    JOIN record_versions v ON v.id = {column}
    JOIN records r ON r.id = v.record_id
    WHERE r.current_version_id != v.id OR r.status NOT IN ('active', 'missing')
"""

CHECKS: dict[str, str] = {
    "foreign keys hold": "SELECT * FROM pragma_foreign_key_check",
    "forms are at their current version":
        "SELECT f.form_key FROM src_forms f" + _CURRENT_VERSION.format(column="f.version_id"),
    "calendar events are at their current version":
        "SELECT e.event_id FROM src_calendar_events e"
        + _CURRENT_VERSION.format(column="coalesce(e.school_version_id, e.activities_version_id)"),
    "calendar copies are at their current version":
        "SELECT e.event_id FROM src_calendar_events e"
        + _CURRENT_VERSION.format(column="e.activities_version_id"),
    "notices are at their current version":
        "SELECT n.notice_key FROM src_notices n" + _CURRENT_VERSION.format(column="n.version_id"),
    "booked and held bookings have a time":
        "SELECT form_key FROM bookings WHERE state IN ('booked', 'held') AND starts_local IS NULL",
    "no child has overlapping booked slots": """
        SELECT a.form_key, b.form_key FROM bookings a
        JOIN bookings b ON b.student_id = a.student_id AND a.form_key < b.form_key
        WHERE a.state = 'booked' AND b.state = 'booked'
          AND a.starts_local < strftime('%Y-%m-%dT%H:%M', b.starts_local, '+' || b.minutes || ' minutes')
          AND b.starts_local < strftime('%Y-%m-%dT%H:%M', a.starts_local, '+' || a.minutes || ' minutes')
    """,
    "every booking's log ends in its state": """
        SELECT b.form_key FROM bookings b
        WHERE b.state IS NOT (SELECT x.to_state FROM booking_changes x
                              WHERE x.student_id = b.student_id AND x.form_key = b.form_key
                              ORDER BY x.id DESC LIMIT 1)
    """,
    "every current fact has a source": """
        SELECT f.stable_key FROM v_current_facts f
        WHERE NOT EXISTS (SELECT 1 FROM derived_item_sources s WHERE s.item_id = f.id)
    """,
    "record states are known":
        "SELECT DISTINCT status FROM records WHERE status NOT IN ('active', 'missing', 'removed')",
    "change types are known": """
        SELECT DISTINCT change_type FROM changes
        WHERE change_type NOT IN ('new', 'content_changed', 'state_changed', 'raw_changed',
                                  'unchanged', 'missing', 'removed')
    """,
}


def check(connection: sqlite3.Connection, *, integrity: bool = True) -> list[tuple[str, list[tuple]]]:
    """The failed checks with up to three offending rows each; empty when all pass.

    Only the names are safe to log: the rows can hold school data.
    `integrity=False` leaves out `PRAGMA integrity_check`, for a caller that
    runs it anyway (the cloud job does, after the WAL checkpoint).
    """
    failures: list[tuple[str, list[tuple]]] = []
    version = current_version(connection)
    if version:
        try:
            check_versions(connection)
        except SchemaError as error:
            return [("schema versions match the code", [(str(error),)])]
    if version != latest_version():
        return [("schema is at the code's version", [(version, latest_version())])]
    if integrity:
        rows = [tuple(row) for row in connection.execute("PRAGMA integrity_check").fetchmany(3)]
        if rows != [("ok",)]:
            failures.append(("integrity_check is ok", rows))
    for name, sql in CHECKS.items():
        rows = [tuple(row) for row in connection.execute(sql).fetchmany(3)]
        if rows:
            failures.append((name, rows))
    return failures
