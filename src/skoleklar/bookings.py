"""Layer 4: each child's own bookings, kept current from the typed sources.

A booking is one child's form of a booking type: a parent conference, or
another appointment such as the school nurse. The scan sets its state and time
from `src_forms` and its length from the round's notice; the database logs
every change itself and refuses moves that `booking_transitions` does not list
(docs/data-foundation.md, "Bookings").

Which forms are bookings, their kinds, their default length and the round
they belong to are the household's `[meaning]` (meaning.py), not code.
"""
from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime
from typing import Any

from skolekit.school import School
from skolekit.items import form_key
from skoleklar.meaning import Meaning

_FIELDS = ("kind", "title", "starts_local", "minutes", "minutes_source", "deadline",
           "notice_key", "window_event_id", "source_version_id")


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def target_state(booked_local: str | None, deadline: str | None, today: date) -> str:
    """The state a form's current status says, before allowed moves are applied."""
    if booked_local:
        return "held" if booked_local[:10] < today.isoformat() else "booked"
    if deadline and deadline < today.isoformat():
        return "missed"
    return "open"


def moves(current: str, target: str, allowed: set[tuple[str, str]]) -> list[str]:
    """The states to pass through from `current` to `target`; empty keeps `current`.

    A slot booked and held between two scans goes through `booked`. A booking
    whose time disappears is `cancelled`. `held` is final, and `cancelled` and
    `missed` stay until a time is booked.
    """
    if current == target:
        return []
    if (current, target) in allowed:
        return [target]
    if target == "held" and (current, "booked") in allowed:
        return ["booked", "held"]
    if current == "booked" and target in ("open", "missed"):
        return ["cancelled"]
    return []


def _first_seen(connection: sqlite3.Connection) -> dict[tuple[int, str], int]:
    """The earliest sighting of each form, across the link-keyed records it had before."""
    first: dict[tuple[int, str], int] = {}
    for row in connection.execute(
            "SELECT student_id, source_key, source_url, first_seen_run_id FROM records "
            "WHERE source_type = 'forms'"):
        key = form_key(row[1], row[2])
        if key is not None:
            identity = (int(row[0]), key)
            first[identity] = min(first.get(identity, row[3]), row[3])
    return first


def _notice(connection: sqlite3.Connection, student_id: int, deadline: str | None) -> sqlite3.Row | None:
    """The round's notice: the child's notice whose sign-up ends on the form's deadline."""
    if deadline is None:
        return None
    return connection.execute(
        """
        SELECT n.notice_key, n.minutes FROM src_notices n
        WHERE n.student_id = ? AND n.signup_until = ?
        ORDER BY n.minutes IS NULL,
                 (SELECT count(*) FROM src_notice_days d
                  WHERE d.student_id = n.student_id AND d.notice_key = n.notice_key) DESC,
                 n.version_id DESC
        LIMIT 1
        """,
        (student_id, deadline)).fetchone()


def _window(connection: sqlite3.Connection, student_id: int, starts_local: str | None,
            school_wide_levels: int) -> int | None:
    """The class's calendar day whose opening hours hold the booked time; a school-wide event isn't one."""
    if starts_local is None:
        return None
    row = connection.execute(
        """
        SELECT e.event_id FROM src_calendar_events e
        JOIN children c ON c.student_id = e.student_id
        WHERE e.student_id = ? AND substr(e.starts_local, 1, 10) = substr(?, 1, 10)
          AND e.starts_local <= ? AND e.ends_local > ?
          AND EXISTS (SELECT 1 FROM src_calendar_event_levels l
                      WHERE l.student_id = e.student_id AND l.event_id = e.event_id
                        AND l.class_level = c.class_level)
          AND (SELECT count(*) FROM src_calendar_event_levels l
               WHERE l.student_id = e.student_id AND l.event_id = e.event_id) < ?
        ORDER BY e.activities_version_id IS NULL, e.event_id
        LIMIT 1
        """,
        (student_id, starts_local, starts_local, starts_local, school_wide_levels)).fetchone()
    return int(row[0]) if row else None


def _write(connection: sqlite3.Connection, student_id: int, key: str, fields: dict[str, Any],
           states: list[str], *, run_id: int, now: str) -> None:
    stamp = {"updated_run_id": run_id, "updated_by": "scan", "updated_at": now}
    steps = states or [None]
    for index, state in enumerate(steps):
        values = {**(fields if index == 0 else {}), **({"state": state} if state else {}), **stamp}
        assignments = ", ".join(f"{column} = ?" for column in values)
        connection.execute(
            f"UPDATE bookings SET {assignments} WHERE student_id = ? AND form_key = ?",
            (*values.values(), student_id, key))


def update(connection: sqlite3.Connection, *, run_id: int, today: date, school: School,
           meaning: Meaning) -> dict[str, Any]:
    """Bring every booking in line with the current forms; one transaction."""
    form_types = tuple(sorted(set(meaning.booking_form_types)))
    school_wide_levels = meaning.school_wide_levels(school)
    now = _now()
    allowed = {(row[0], row[1]) for row in connection.execute(
        "SELECT from_state, to_state FROM booking_transitions")}
    first_seen = _first_seen(connection)
    counts = {"new": 0, "changed": 0, "unchanged": 0, "skipped_owner": 0}
    if not connection.in_transaction:
        connection.execute("BEGIN IMMEDIATE")
    try:
        forms = connection.execute(
            "SELECT * FROM src_forms WHERE type_label IN ({}) ORDER BY student_id, form_key".format(
                ", ".join("?" for _ in form_types)),
            form_types).fetchall()
        present: set[tuple[int, str]] = set()
        for form in forms:
            student_id, key = int(form["student_id"]), form["form_key"]
            present.add((student_id, key))
            existing = connection.execute(
                "SELECT * FROM bookings WHERE student_id = ? AND form_key = ?",
                (student_id, key)).fetchone()
            if (existing is not None and existing["updated_by"] == "owner"
                    and existing["source_version_id"] == form["version_id"]):
                counts["skipped_owner"] += 1
                continue
            notice = _notice(connection, student_id, form["deadline"])
            starts = form["booked_local"] or (existing["starts_local"] if existing else None)
            if existing is not None and existing["minutes_source"] == "owner":
                minutes, minutes_source = existing["minutes"], "owner"
            elif notice is not None and notice["minutes"]:
                minutes, minutes_source = int(notice["minutes"]), "notice"
            else:
                minutes, minutes_source = meaning.default_minutes, "default"
            fields = {
                "kind": meaning.booking_kind(form["title"]),
                "title": form["title"],
                "starts_local": starts,
                "minutes": minutes,
                "minutes_source": minutes_source,
                "deadline": form["deadline"],
                "notice_key": notice["notice_key"] if notice is not None else None,
                "window_event_id": _window(connection, student_id, starts, school_wide_levels),
                "source_version_id": form["version_id"],
            }
            target = target_state(form["booked_local"], form["deadline"], today)
            if existing is None:
                connection.execute(
                    f"""
                    INSERT INTO bookings(student_id, form_key, state, first_seen_run_id,
                                         updated_run_id, updated_by, updated_at, {", ".join(_FIELDS)})
                    VALUES (?, ?, ?, ?, ?, 'scan', ?, {", ".join("?" for _ in _FIELDS)})
                    """,
                    (student_id, key, target, first_seen.get((student_id, key), run_id), run_id, now,
                     *(fields[column] for column in _FIELDS)))
                counts["new"] += 1
                continue
            states = moves(existing["state"], target, allowed)
            changed = {column: value for column, value in fields.items() if existing[column] != value}
            if not states and not changed:
                counts["unchanged"] += 1
                continue
            _write(connection, student_id, key, changed, states, run_id=run_id, now=now)
            counts["changed"] += 1
        # A booking whose form is gone: a slot already passed was held, anything
        # else still pending is cancelled.
        for booking in connection.execute(
                "SELECT * FROM bookings WHERE state IN ('open', 'booked')").fetchall():
            identity = (int(booking["student_id"]), booking["form_key"])
            if identity in present or booking["updated_by"] == "owner":
                continue
            passed = booking["starts_local"] and booking["starts_local"][:10] < today.isoformat()
            target = "held" if booking["state"] == "booked" and passed else "cancelled"
            _write(connection, *identity, {}, moves(booking["state"], target, allowed),
                   run_id=run_id, now=now)
            counts["changed"] += 1
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    states = {row[0]: row[1] for row in connection.execute(
        "SELECT state, count(*) FROM bookings GROUP BY state ORDER BY state")}
    return {**counts, "states": states}
