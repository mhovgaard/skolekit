"""How well a school's rules fit the pages the household stored: `skoleklar school check`.

The counting is skolekit's (skolekit.check), shared with the live
`skolekit school check`; this module feeds it the current version of every
stored item. Counts only, never text.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from skolekit.check import count_rules, form_status, plan_lessons
from skolekit.school import School
from skoleklar.store.typed import _current


def school_check(connection: sqlite3.Connection, school: School) -> dict[str, Any]:
    """Per rule, how many current stored items it matched, did not match, and refused."""
    notices = _current(connection, "frontpage")
    rules = count_rules(
        school.wording,
        statuses=[form_status(json.loads(record["payload_json"])) for record in _current(connection, "forms")],
        notice_texts=[str(record["body_text"] or "") for record in notices],
        broad_texts=[f"{record['title'] or ''} {record['body_text'] or ''}"
                     for record in [*_current(connection, "calendar"), *notices]],
        lesson_texts=[text for record in _current(connection, "weekly_plan")
                      for text in plan_lessons(json.loads(record["payload_json"]).get("source_data") or {})],
    )
    return {"school": school.digest(), "rules": rules}


def check_database(path: Path, school: School) -> dict[str, Any]:
    """`school_check` on a database opened read-only."""
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only = ON")
        return school_check(connection, school)
    finally:
        connection.close()
