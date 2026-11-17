#!/usr/bin/env python3
"""Settle the facts flagged for review before the board is written, so it heals itself.

An earlier step flags a fact it is unsure of (`needs_review`), and the board leaves
it out and counts it as `N punkter at tjekke`. Each scan now checks the flagged
facts in the board's period instead of leaving them to the parents:

1. Rules, no model: a fact marked for another class level than the child's is
   dropped; a vague fact that a specific fact for the same child and day covers
   (`Skole-hjem-samtale … kl. 15`) is dropped.
2. The model, for the rest that carry evidence: the stored quotes and the other
   facts for that child and day go through the zero-data-retention route
   (prompt `review-check-v1`); it answers `show` (with an optional note such as
   `tid ikke oplyst`), `drop` or `unclear` (with a short question for the parents).

A verdict is stored with a fingerprint of the fact (`review_verdicts`), so a fact
is checked once and again only when it changes; every scan retries what is still
open. The board applies the verdicts when it exports its context: `show` clears
the flag, `drop` leaves the fact out, `unclear` keeps it counted. If the check
fails, nothing is stored and the board behaves as before. See docs/processing.md#review-checks.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from skoleklar.household import load_site_profile
from skolekit.school import CLASS_LABEL

VERDICTS = ("show", "drop", "unclear")
# The board's period: the next school day, four days ahead and two weeks of events.
WINDOW_DAYS = 18
MAX_MODEL_ITEMS = 8
NOTE_LIMIT = 40
REASON_LIMIT = 200
_CLOCK = re.compile(r"\bkl\.?\s*\d{1,2}(?:[.:]\d{2})?\b|\b\d{1,2}[.:]\d{2}\b")


def _kind(item: dict) -> str:
    return str(item.get("item_type") or item.get("type") or "")


def fingerprint(item: dict) -> str:
    """What the verdict depends on: a changed fact is checked again."""
    document = {key: item.get(key) for key in ("child", "title", "summary", "effective_date",
                                               "end_date", "review_reason")}
    document["kind"] = _kind(item)
    return hashlib.sha256(json.dumps(document, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def stored_verdicts(store) -> dict[tuple[str, str], dict]:
    """Every stored verdict by (stable key, fingerprint); none for an older database."""
    try:
        rows = store.connection.execute("SELECT * FROM review_verdicts").fetchall()
    except sqlite3.OperationalError:
        return {}
    return {(row["stable_key"], row["fingerprint"]): dict(row) for row in rows}


def apply_verdicts(store, items: list[dict]) -> list[dict]:
    """The board's facts with the stored verdicts applied; other facts are unchanged."""
    verdicts = stored_verdicts(store)
    if not verdicts:
        return items
    result = []
    for item in items:
        verdict = verdicts.get((str(item.get("stable_key")), fingerprint(item))) \
            if item.get("needs_review") else None
        if verdict is None or verdict["verdict"] == "unclear":
            result.append(item)
        elif verdict["verdict"] == "show":
            title = str(item.get("title") or "")
            note = verdict.get("note")
            result.append({**item, "needs_review": False,
                           "title": f"{title} · {note}" if note else title})
    return result


def child_classes(store=None, children=None) -> dict[str, int]:
    """Each child's class level from the household's profile ('002' is 2. klasse)."""
    if children is None:
        children = load_site_profile().children
    return {child.child: child.class_level for child in children if child.class_level is not None}


def class_label() -> str:
    """How the school writes a class, from its `[classes] label`: '{level}. klasse'."""
    return load_site_profile().school.class_label


def _quotes(item: dict) -> list[str]:
    return [str(entry.get("quote") or "")[:200] for entry in item.get("evidence") or []
            if isinstance(entry, dict) and entry.get("quote")]


def _stems(item: dict) -> set[str]:
    words = re.findall(r"[a-zæøå]+", str(item.get("title") or "").casefold().replace("-", ""))
    return {word[:7] for word in words if len(word) >= 6}


def rule_verdict(item: dict, items: list[dict], classes: dict[str, int],
                 label: str = CLASS_LABEL) -> tuple[str, str | None, str] | None:
    """(verdict, note, reason) when a rule settles the fact without a model, else None."""
    child = item.get("child")
    own = classes.get(child)
    # The class levels the extractor says the fact concerns (skoleklar.fact), never its words.
    mentioned = set(item.get("class_levels") or [])
    if own is not None and mentioned and own not in mentioned:
        levels = ", ".join(label.format(level=number) for number in sorted(mentioned))
        return "drop", None, f"Marked for {levels}; {child} is in {label.format(level=own)}."
    for other in items:
        if (other is item or other.get("needs_review") or other.get("child") != child
                or other.get("effective_date") != item.get("effective_date")
                or _kind(other) != _kind(item) or other.get("dismissed_at")):
            continue
        # Specific means about this child by name with a time: 'Skole-hjem-samtale for
        # Beta … kl. 15.00'. A class-wide window ('kl. 14.00-19.00') is not.
        other_text = f"{other.get('title') or ''} {other.get('summary') or ''}"
        if (str(child).casefold() in other_text.casefold() and _CLOCK.search(other_text)
                and _stems(item) & _stems(other)):
            return "drop", None, f"Covered by a specific fact for {child} that day."
    return None


def _clean(value: Any, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    text = re.sub(r"\s+", " ", value).strip()
    if not text or len(text) > limit or any(ord(character) < 32 for character in text):
        return None
    return text


def model_verdicts(client, today: date, classes: dict[str, int], entries: list[dict],
                   items: list[dict], label: str = CLASS_LABEL
                   ) -> dict[str, tuple[str, str | None, str]]:
    """Verdicts by stable key from one model call; invalid answers are left out."""
    from skoleklar.triage.prompt_manifest import load_prompt_manifest

    names = sorted({str(item.get("child")) for item in items if item.get("child")})
    document = {
        "today": today.isoformat(),
        "children": [{"name": name, "class_level": label.format(level=classes[name]) if name in classes else None}
                     for name in names],
        "items": [],
    }
    keys = {}
    for index, item in enumerate(entries, 1):
        keys[str(index)] = str(item["stable_key"])
        same_day = [{"child": other.get("child"), "title": other.get("title"),
                     "summary": str(other.get("summary") or "")[:200], "flagged": bool(other.get("needs_review"))}
                    for other in items if other is not item and other.get("child") == item.get("child")
                    and other.get("effective_date") == item.get("effective_date")][:8]
        document["items"].append({
            "id": str(index), "child": item.get("child"), "type": _kind(item), "title": item.get("title"),
            "summary": str(item.get("summary") or "")[:300], "date": item.get("effective_date"),
            "review_reason": str(item.get("review_reason") or "")[:300], "evidence": _quotes(item)[:6],
            "same_day": same_day,
        })
    response = client.generate(document, manifest=load_prompt_manifest("review-check-v1"),
                               max_output_tokens=1500)
    result = {}
    for entry in (response.get("output") or {}).get("verdicts") or []:
        if not isinstance(entry, dict) or entry.get("id") not in keys or entry.get("verdict") not in VERDICTS:
            continue
        note = _clean(entry.get("note"), NOTE_LIMIT) if entry.get("note") is not None else None
        reason = _clean(entry.get("reason"), REASON_LIMIT) or "Model verdict."
        result.setdefault(keys[entry["id"]], (entry["verdict"], note, reason))
    return result


def check_reviews(store, *, today: date, client=None, message_mode: str = "events-only",
                  now: datetime | None = None) -> dict[str, int]:
    """Check the flagged facts in the board's period and store the verdicts.

    Returns counts for the log. `client=None` runs the rules only.
    """
    from skoleklar.delivery_manifest import delivery_items_from_snapshot

    items = [item for item in delivery_items_from_snapshot(store, message_mode=message_mode)
             if not item.get("completed_at") and not item.get("dismissed_at")]
    last = today + timedelta(days=WINDOW_DAYS)
    flagged = [item for item in items if item.get("needs_review") and item.get("stable_key")
               and item.get("effective_date") and today.isoformat() <= item["effective_date"] <= last.isoformat()]
    known = stored_verdicts(store)
    open_items = [item for item in flagged if (str(item["stable_key"]), fingerprint(item)) not in known]
    counts = {"flagged": len(flagged), "reused": len(flagged) - len(open_items),
              "rule": 0, "model": 0, "unclear": 0, "unchecked": 0, "model_failed": 0}
    classes, label = child_classes(store), class_label()
    decided: list[tuple[dict, str, str | None, str, str]] = []
    remaining = []
    for item in open_items:
        verdict = rule_verdict(item, items, classes, label)
        if verdict:
            decided.append((item, *verdict, "rule"))
            counts["rule"] += 1
        else:
            remaining.append(item)
    # The model needs something to read: facts without evidence wait for a richer source.
    askable = [item for item in remaining if _quotes(item)][:MAX_MODEL_ITEMS]
    counts["unchecked"] = len(remaining) - len(askable)
    if client is not None and askable:
        try:
            answers = model_verdicts(client, today, classes, askable, items, label)
        except Exception:
            # The rules' verdicts still count; the rest is retried by the next scan.
            answers = {}
            counts["model_failed"] = 1
        for item in askable:
            if str(item["stable_key"]) in answers:
                verdict, note, reason = answers[str(item["stable_key"])]
                decided.append((item, verdict, note, reason, "model"))
                counts["model"] += 1
                counts["unclear"] += verdict == "unclear"
            else:
                counts["unchecked"] += 1
    elif askable:
        counts["unchecked"] += len(askable)
    stamp = (now or datetime.now().astimezone()).isoformat()
    with store.connection:
        store.connection.executemany(
            "INSERT OR REPLACE INTO review_verdicts "
            "(stable_key, fingerprint, verdict, note, reason, method, decided_at) VALUES (?,?,?,?,?,?,?)",
            [(str(item["stable_key"]), fingerprint(item), verdict, note, reason, method, stamp)
             for item, verdict, note, reason, method in decided],
        )
    return counts


def main() -> None:
    """Ad hoc check: list the flagged facts in the board's period and what would happen."""
    from skoleklar.delivery_manifest import delivery_items_from_snapshot
    from skoleklar.storage import SnapshotStore

    parser = argparse.ArgumentParser(description=main.__doc__)
    from skolekit.state import state_dir

    parser.add_argument("--db", type=Path, default=state_dir() / "school-restricted.db")
    parser.add_argument("--today", type=date.fromisoformat, default=date.today())
    args = parser.parse_args()
    with SnapshotStore(args.db, read_only=True) as store:
        items = [item for item in delivery_items_from_snapshot(store, message_mode="events-only")
                 if not item.get("completed_at") and not item.get("dismissed_at")]
        known, classes, label = stored_verdicts(store), child_classes(store), class_label()
        last = (args.today + timedelta(days=WINDOW_DAYS)).isoformat()
        for item in items:
            if not item.get("needs_review") or not item.get("effective_date") \
                    or not args.today.isoformat() <= item["effective_date"] <= last:
                continue
            stored = known.get((str(item.get("stable_key")), fingerprint(item)))
            rule = rule_verdict(item, items, classes, label)
            outcome = (f"stored {stored['verdict']} ({stored['method']})" if stored
                       else f"rule {rule[0]}" if rule else "model" if _quotes(item) else "waits")
            print(json.dumps({"date": item["effective_date"], "child": item.get("child"),
                              "title": item.get("title"), "outcome": outcome}, ensure_ascii=False))


if __name__ == "__main__":
    main()
