"""One scan's update of the formal store: typed sources, then household state.

The steps follow docs/data-foundation.md ("Operation"): after collection and
before the model, rebuild layer 2 from the current record versions, then bring
layer 4 (bookings) in line with it. Each step is one transaction.
"""
from __future__ import annotations

import sqlite3
from datetime import date
from typing import Any

from skolekit.profile import SiteProfile
from skoleklar.store import typed as typed_sources
from skoleklar import bookings
from skoleklar.meaning import Meaning


def refresh(connection: sqlite3.Connection, *, run_id: int, today: date, profile: SiteProfile,
            meaning: Meaning) -> dict[str, Any]:
    """The typed sources with the school's wording, then the bookings with the household's meaning."""
    typed = typed_sources.rebuild(connection, run_id=run_id, children=profile.children,
                                  school=profile.school)
    return {"typed_sources": typed,
            "bookings": bookings.update(connection, run_id=run_id, today=today,
                                        school=profile.school, meaning=meaning)}
