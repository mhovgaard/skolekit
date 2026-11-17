"""The validated week view: what the household's school days hold, as the board sees them.

One read service exposes checked current facts, restricted message facts
and bookings over a bounded period. It reads stored records without model
or school requests; traceability is available through the separate query.

- the facts after consolidation, with the review checks' verdicts applied (a
  dropped fact is left out, a shown one keeps its note);
- the facts the restricted extraction made from messages, never their text;
- the bookings.

It calls no model and never reaches the school. It reads a database, so it
answers from a pulled copy too, and the phone, the board and `skoleklar mcp`
stand on the same records. Evidence quotes, source links and message handles
are left out: the view says what is happening, and `trace` says where from.

"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from skoleklar import fact as fact_model
from skoleklar.delivery_manifest import delivery_items_from_snapshot
from skoleklar.triage.review_checks import apply_verdicts

SCHEMA_VERSION = 1
# What a fact in the view holds (skoleklar.fact); anything else stays in the store.
FACT_FIELDS = fact_model.WEEK_CORE


def _fact(item: dict[str, Any]) -> dict[str, Any]:
    # What the fact is (skoleklar.fact), as its extractor said it.
    fact = {field: item.get(field) for field in FACT_FIELDS} | {
        key: value for key, value in fact_model.public(item).items() if key != "type"}
    fact["needs_review"] = bool(fact["needs_review"])
    fact["all_day"] = bool(fact["all_day"])
    fact["from"] = "messages" if "messages" in (item.get("source_types") or []) else "school"
    return fact


def _overlaps(item: dict[str, Any], start: str, end: str) -> bool:
    first = str(item.get("effective_date") or "")[:10]
    if not first:
        return False
    last = str(item.get("end_date") or first)[:10]
    return first <= end and last >= start


def week_view(store: Any, *, start: date, days: int = 7, child: str | None = None,
              include_undated: bool = False) -> dict[str, Any]:
    """The checked facts and bookings for `days` days from `start`, for one child or all."""
    if not 1 <= days <= 31:
        raise ValueError("days must be from 1 to 31")
    end = start + timedelta(days=days - 1)
    first, last = start.isoformat(), end.isoformat()
    run = store.connection.execute(
        "SELECT completed_at FROM harvest_runs WHERE status IN ('complete', 'partial') "
        "AND completed_at IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()
    items = apply_verdicts(store, delivery_items_from_snapshot(store, message_mode="events-only"))
    facts = []
    for item in items:
        if item.get("dismissed_at") or (child is not None and item.get("child") not in (child, "household")):
            continue
        dated = bool(item.get("effective_date"))
        if (dated and _overlaps(item, first, last)) or (not dated and include_undated):
            facts.append(_fact(item))
    facts.sort(key=lambda fact: (fact["effective_date"] or "9999", str(fact["child"]), fact["title"] or ""))
    bookings = [dict(row) for row in store.connection.execute(
        "SELECT child, kind, title, state, starts_local, minutes, deadline FROM v_bookings "
        "WHERE (substr(starts_local, 1, 10) BETWEEN ? AND ?) "
        "   OR (state = 'open' AND deadline BETWEEN ? AND ?) "
        "ORDER BY coalesce(starts_local, deadline), child", (first, last, first, last))
                if child is None or row["child"] == child]
    return {"schema_version": SCHEMA_VERSION, "source": "store",
            "as_of": run["completed_at"] if run else None, "from": first, "to": last, "child": child,
            "facts": facts, "bookings": bookings}
