#!/usr/bin/env python3
"""Export bounded, filtered local context for agent composition; no network calls."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from skoleklar import fact as fact_model
from skoleklar.brief import build_brief_range
from skoleklar.dashboard_timing import dashboard_focus, new_messages_since
from skoleklar.dashboard_events import (ACTIVITY_LABELS, activity_title, birthday_notice, confirmed_activity,
                                        event_confirmations)
from skoleklar.delivery_manifest import delivery_items_from_snapshot
from skoleklar.triage.review_checks import apply_verdicts
from skoleklar.storage import SnapshotStore

ZONE = ZoneInfo("Europe/Copenhagen")
FIELDS = ("type", "child", "title", "summary", "effective_date", "end_date",
          "actionability", "urgency", "confidence", "needs_review")
# Facts that are the same thing for the board: everything but their reference.
_SAME_FACT = ("type", "child", "title", "effective_date", "end_date", "actionability",
              "private_message_fact", "cancelled", "activity_kind", "confirmed_activity_kind",
              "message_thread", "birthday_notice")


def meaning(item):
    """What a fact is (skoleklar.fact), leaving out what is empty for it."""
    return {key: value for key, value in fact_model.meaning(item).items()
            if key != "contract" and value not in (None, [], False)}


def collapse_duplicates(items):
    """One fact per distinct thing; repeats of the same fact add to its `repeats` count.

    Five identical substitute periods or a notice published to both classes under
    one child become one fact, so the layout step never has to find them. What
    the fact is joins as consolidation joins it (`skoleklar.fact.merge`).
    """
    kept = {}
    result = []
    for item in items:
        key = tuple(re.sub(r"\s+", " ", str(item.get(k) or "")).strip().casefold() for k in _SAME_FACT)
        if key in kept:
            first = kept[key]
            first["repeats"] = first.get("repeats", 1) + 1
            joined = fact_model.merge(first.get("type"), [{**first, "contract": fact_model.CONTRACT},
                                                         {**item, "contract": fact_model.CONTRACT}])
            for field in fact_model.names():
                first.pop(field, None)
            first.update(meaning(joined))
            continue
        kept[key] = item
        result.append(item)
    return result


def arrived_since(activity: str | None, since: datetime) -> bool:
    """Whether a thread's latest activity is at or after `since`.

    The inbox gives a clock time only for recent messages. A bare date counts when
    it is the cutoff's day or later, so yesterday evening is never missed.
    """
    if not activity:
        return False
    try:
        if "T" in activity:
            return datetime.fromisoformat(activity).replace(tzinfo=None) >= since.replace(tzinfo=None)
        return date.fromisoformat(activity) >= since.date()
    except ValueError:
        return False


def export_context(store, *, now, days=10, stale_hours=36, message_mode="events-only",
                   confirmations=None):
    if now.tzinfo is None:
        raise ValueError("--now requires a timezone")
    now = now.astimezone(ZONE)
    confirmations = event_confirmations(confirmations)
    # Use one SQLite snapshot for brief, source coverage and inbox projection.
    run = store.connection.execute("SELECT * FROM harvest_runs ORDER BY id DESC LIMIT 1").fetchone()
    if run is None or not run["completed_at"]:
        raise ValueError("No finished latest harvest; finish the lifecycle first")
    updated = datetime.fromisoformat(run["completed_at"]).astimezone(ZONE)
    checks = store.connection.execute(
        "SELECT child,source_type,status,metadata_json FROM source_checks WHERE run_id=?",
        (run["id"],),
    ).fetchall()
    source_checks = []
    for row in checks:
        metadata = json.loads(row["metadata_json"])
        source_checks.append({"child": row["child"], "source": row["source_type"],
                              "status": row["status"],
                              **{k: metadata[k] for k in ("pages", "exhausted", "weeks", "range_start", "range_end") if k in metadata}})
    status = "fresh"
    if run["status"] != "complete" or any(c["status"] != "ok" for c in checks):
        status = "partial"
    elif now < updated or now - updated > timedelta(hours=stale_hours):
        status = "stale"
    # The review check's stored verdicts settle facts that were flagged for review.
    items = apply_verdicts(store, delivery_items_from_snapshot(store, message_mode=message_mode))
    hero_date = dashboard_focus(now).school_date
    week_start = hero_date - timedelta(days=hero_date.weekday())
    start = min(now.date(), week_start)
    end = max(now.date() + timedelta(days=days - 1), week_start + timedelta(days=6))
    brief = build_brief_range(items, start_date=start, end_date=end, compact=True)
    # Facts from one message thread may describe one event. Give the model a
    # neutral per-export number instead of the opaque thread handle.
    thread_of = {item["stable_key"]: item["message_thread_ref"]
                 for item in items if item.get("message_thread_ref")}
    thread_numbers = {}
    context_items = []
    # Facts made under an older contract than the board reads; the scan re-derives them.
    stale_contract = 0
    for index, item in enumerate(brief["items"], 1):
        fact = {"ref": f"fact-{index}", **{k: item[k] for k in FIELDS},
                "private_message_fact": item["stable_key"].startswith("message-event:"),
                "grouped": bool(item.get("grouped")), **meaning(item)}
        if not fact_model.current(item):
            stale_contract += 1
        if item.get("state") == "cancelled":
            fact["cancelled"] = True
        thread = thread_of.get(item["stable_key"])
        if thread:
            fact["message_thread"] = thread_numbers.setdefault(thread, len(thread_numbers) + 1)
        if birthday_notice(fact):
            fact["birthday_notice"] = True
        kind = confirmed_activity(item, confirmations)
        if kind:
            fact["confirmed_activity_kind"] = kind
            fact["title"] = activity_title(kind, item.get("celebrant"))
            fact["summary"] = "Brugerbekræftet aktivitet; tidspunkt er ikke bekræftet."
        elif item.get("type") == "event" and item.get("activity_kind") in ACTIVITY_LABELS:
            # Extracted bounded category; a weekend activity the family should see.
            fact["activity_kind"] = item["activity_kind"]
            # 'Emmas fødselsdag' when the invitation names the birthday child.
            fact["title"] = activity_title(item["activity_kind"], item.get("celebrant"))
        context_items.append(fact)
    context_items = collapse_duplicates(context_items)
    children = sorted({row["child"] for row in checks})
    weekly_plans = []
    for row in store.connection.execute(
        "SELECT r.child,r.source_key FROM records r JOIN run_observations o ON o.record_id=r.id "
        "WHERE o.run_id=? AND r.source_type='weekly_plan'", (run["id"],)
    ):
        match = re.fullmatch(r"class:(\d{1,2})-(\d{4})", row["source_key"])
        if match:
            weekly_plans.append({"child": row["child"], "week": int(match[1]), "year": int(match[2])})
    message_checks = [c for c in checks if c["source_type"] == "messages"]
    checked = (message_mode == "events-only" and status == "fresh" and bool(children)
               and {c["child"] for c in message_checks if c["status"] == "ok"} == set(children))
    coverage = store.message_coverage(mode=message_mode, processor="disabled")
    rows = []
    new_since = new_messages_since(now)
    if message_mode == "events-only":
        for row in store.message_thread_states():
            if row["exists_now"]:
                rows.append({"participants": row["participants"], "child": row["child"],
                             "activity": row["latest_activity_at"], "observed_at": row["observed_at"],
                             "unread": row["server_unread"], "new_thread": row["is_new_thread"],
                             "new_message": row["has_new_message"],
                             "latest_unretrieved": row["latest_unretrieved"],
                             "new": arrived_since(row["latest_activity_at"], new_since)})
    messages = {"checked": checked, "bodies_retained": 0, "unread_count": coverage["unread_count"],
                "new_count": sum(row["new"] for row in rows), "items": rows}
    # Bodies, evidence, URLs, stable keys and opaque inbox handles never enter this file.
    return {
        "context_version": 2, "now": now.isoformat(), "timezone": str(ZONE),
        "from": start.isoformat(), "to": end.isoformat(), "children": children,
        "freshness": {"source_updated_at": updated.isoformat(), "composed_at": now.isoformat(),
                      "expires_at": (updated + timedelta(hours=stale_hours)).isoformat(), "status": status},
        "harvest_run_id": run["id"], "source_checks": source_checks, "weekly_plans": weekly_plans,
        "items": context_items, "atomic_item_count": brief["atomic_item_count"],
        "messages": messages, "contract": {"current": fact_model.CONTRACT, "stale": stale_contract},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    from skolekit.state import state_dir

    parser.add_argument("--db", type=Path, default=state_dir() / "school-restricted.db")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--now", type=datetime.fromisoformat, default=None)
    parser.add_argument("--days", type=int, default=10)
    parser.add_argument("--stale-hours", type=int, default=36)
    parser.add_argument("--message-mode", choices=["events-only", "off"], default="events-only")
    parser.add_argument("--event-confirmations", type=Path, help="Private categorical owner confirmations")
    args = parser.parse_args()
    if not 1 <= args.days <= 21 or args.stale_hours <= 0:
        parser.error("days must be 1–21; stale-hours must be positive")
    with SnapshotStore(args.db, read_only=True) as store:
        store.connection.execute("BEGIN")
        result = export_context(store, now=args.now or datetime.now(ZONE), days=args.days,
                                stale_hours=args.stale_hours, message_mode=args.message_mode,
                                confirmations=json.loads(args.event_confirmations.read_text())
                                if args.event_confirmations else None)
    args.output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        os.fchmod(handle.fileno(), 0o600)
        json.dump(result, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(json.dumps({"context": str(args.output.resolve()), "item_count": len(result["items"]),
                      "freshness": result["freshness"]["status"]}))


if __name__ == "__main__":
    main()
