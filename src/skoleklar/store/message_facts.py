"""Layer 3 for messages: the facts the restricted extraction made, its receipts and
attempts, and the owner's feedback. Never message text: a fact holds its kind, dates,
what to bring, a place and shortened names.

`SnapshotStore` combines this restricted repository with the other layers.
Retain only allowlisted facts and operational metadata; never source text.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import date
from typing import Any, Iterable

from skoleklar.store.capture import utc_now

# Danish titles for message facts, shown on the phone and the board alike.
MESSAGE_ACTIVITY_TITLES = {
    "birthday_party": "Fødselsdagsfest",
    "class_trip": "Klassetur",
    "class_event": "Klassearrangement",
    "social_activity": "Socialt arrangement",
}


MESSAGE_EVENT_TITLES = {
    "event": "Skolearrangement",
    "schedule_change": "Skemaændring",
    "cancellation": "Arrangement aflyst",
}


_SHORT_DAYS = ("man.", "tir.", "ons.", "tor.", "fre.", "lør.", "søn.")


_SHORT_MONTHS = ("jan.", "feb.", "mar.", "apr.", "maj", "jun.", "jul.", "aug.", "sep.", "okt.",
                 "nov.", "dec.")


def _sentence(text: str) -> str:
    """`text` ending in exactly one full stop, also after `15. sep.` or `Anne J.`."""
    return text if text.endswith(".") else text + "."


def danish_short_date(value: str) -> str:
    """`søn. 8. nov.` for `2026-11-08`; an unreadable date is returned as given."""
    try:
        day = date.fromisoformat(str(value)[:10])
    except ValueError:
        return str(value)
    return f"{_SHORT_DAYS[day.weekday()]} {day.day}. {_SHORT_MONTHS[day.month - 1]}"


class MessageFactRepository:
    connection: sqlite3.Connection

    def complete_message_event_extraction(
        self,
        *,
        thread_ref: str,
        message_ref: str,
        facts: Iterable[dict[str, Any]],
        extractor_version: str,
        policy_version: str,
    ) -> list[int]:
        facts_list = list(facts)
        now = utc_now()
        item_ids: list[int] = []
        if not re.fullmatch(r"thr_[a-z2-7]{16}", thread_ref):
            raise ValueError("Invalid opaque thread reference")
        if not re.fullmatch(r"msg_[a-z2-7]{16}", message_ref):
            raise ValueError("Invalid opaque message reference")
        for fact in facts_list:
            self._validate_message_event_fact(fact)
        try:
            self.connection.execute(
                """
                UPDATE message_event_facts
                SET workflow_state = 'superseded', last_confirmed_at = ?
                WHERE thread_ref = ? AND message_ref = ?
                  AND extractor_version != ?
                  AND workflow_state IN ('active', 'cancelled')
                """,
                (now, thread_ref, message_ref, extractor_version),
            )
            for fact in facts_list:
                cursor = self.connection.execute(
                    """
                    INSERT INTO message_event_facts(
                        stable_key, thread_ref, message_ref, child, event_kind,
                        effective_date, end_date, all_day, cancellation_state,
                        necessary_location, bring_items_json, requirement,
                        activity_kind, participant_display_json, template_key,
                        confidence, needs_review, workflow_state, extractor_version,
                        policy_version, first_observed_at, last_confirmed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(stable_key) DO UPDATE SET
                        message_ref = excluded.message_ref,
                        child = excluded.child,
                        event_kind = excluded.event_kind,
                        effective_date = excluded.effective_date,
                        end_date = excluded.end_date,
                        all_day = excluded.all_day,
                        cancellation_state = excluded.cancellation_state,
                        necessary_location = excluded.necessary_location,
                        bring_items_json = excluded.bring_items_json,
                        requirement = excluded.requirement,
                        activity_kind = excluded.activity_kind,
                        participant_display_json = excluded.participant_display_json,
                        template_key = excluded.template_key,
                        confidence = excluded.confidence,
                        needs_review = excluded.needs_review,
                        workflow_state = excluded.workflow_state,
                        extractor_version = excluded.extractor_version,
                        policy_version = excluded.policy_version,
                        last_confirmed_at = excluded.last_confirmed_at
                    """,
                    (
                        fact["stable_key"],
                        thread_ref,
                        message_ref,
                        fact["child"],
                        fact["event_kind"],
                        fact["effective_date"],
                        fact.get("end_date"),
                        int(bool(fact["all_day"])),
                        fact.get("cancellation_state", "active"),
                        fact.get("necessary_location"),
                        json.dumps(
                            fact.get("bring_items") or [],
                            ensure_ascii=False,
                        ),
                        fact.get("requirement"),
                        fact.get("activity_kind"),
                        json.dumps(
                            fact.get("participant_display_names") or [],
                            ensure_ascii=False,
                        ),
                        fact["template_key"],
                        float(fact["confidence"]),
                        int(bool(fact["needs_review"])),
                        fact.get("workflow_state", "active"),
                        extractor_version,
                        policy_version,
                        now,
                        now,
                    ),
                )
                item_id = cursor.lastrowid
                if not item_id:
                    row = self.connection.execute(
                        "SELECT id FROM message_event_facts WHERE stable_key = ?",
                        (fact["stable_key"],),
                    ).fetchone()
                    assert row is not None
                    item_id = row["id"]
                item_ids.append(int(item_id))
                self.connection.execute(
                    """
                    INSERT INTO item_workflow(stable_key, first_seen_at, last_seen_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(stable_key) DO UPDATE SET
                        last_seen_at = excluded.last_seen_at
                    """,
                    (fact["stable_key"], now, now),
                )
            self.connection.execute(
                """
                INSERT INTO message_processing_receipts(
                    thread_ref, message_ref, result_code, extractor_version,
                    policy_version, processed_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(thread_ref, message_ref) DO UPDATE SET
                    result_code = excluded.result_code,
                    extractor_version = excluded.extractor_version,
                    policy_version = excluded.policy_version,
                    processed_at = excluded.processed_at
                """,
                (
                    thread_ref,
                    message_ref,
                    "events" if facts_list else "no_events",
                    extractor_version,
                    policy_version,
                    now,
                ),
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return item_ids

    @staticmethod
    def _validate_message_event_fact(fact: dict[str, Any]) -> None:
        allowed = {
            "stable_key",
            "child",
            "event_kind",
            "activity_kind",
            "effective_date",
            "end_date",
            "all_day",
            "cancellation_state",
            "necessary_location",
            "bring_items",
            "requirement",
            "participant_display_names",
            "template_key",
            "confidence",
            "needs_review",
            "workflow_state",
        }
        required = {
            "stable_key",
            "child",
            "event_kind",
            "effective_date",
            "all_day",
            "template_key",
            "confidence",
            "needs_review",
        }
        if set(fact) - allowed or not required <= set(fact):
            raise ValueError("Message event fact does not match the privacy schema")
        if fact["event_kind"] not in {
            "event",
            "schedule_change",
            "cancellation",
            "bring",
        }:
            raise ValueError("Unsupported message event kind")
        if fact.get("activity_kind") is not None and (
            fact["event_kind"] != "event"
            or fact["activity_kind"] not in MESSAGE_ACTIVITY_TITLES
        ):
            raise ValueError("Unsupported message activity kind")
        try:
            effective_date = str(fact["effective_date"])
            if date.fromisoformat(effective_date).isoformat() != effective_date:
                raise ValueError
            if fact.get("end_date"):
                end_date = str(fact["end_date"])
                if date.fromisoformat(end_date).isoformat() != end_date:
                    raise ValueError
        except ValueError as error:
            raise ValueError("Message event dates must be ISO dates") from error
        if not re.fullmatch(r"message-event:[a-f0-9]{64}", str(fact["stable_key"])):
            raise ValueError("Invalid message event stable key")
        if fact["template_key"] not in {
            "private_event",
            "private_schedule_change",
            "private_cancellation",
            "private_bring",
        }:
            raise ValueError("Invalid message event template key")
        participants = fact.get("participant_display_names") or []
        if not isinstance(participants, list) or any(
            not isinstance(name, str)
            or not name
            or len(name) > 100
            or "\n" in name
            or len(name.split()) > 2
            for name in participants
        ):
            raise ValueError("Invalid minimized event participant name")
        child = str(fact["child"])
        if not child or len(child) > 80 or "\n" in child:
            raise ValueError("Invalid message event child label")
        location = fact.get("necessary_location")
        if location is not None and (
            not isinstance(location, str)
            or len(location) > 120
            or "\n" in location
        ):
            raise ValueError("Message event location is not privacy-safe")
        bring_items = fact.get("bring_items") or []
        requirement = fact.get("requirement")
        if fact["event_kind"] == "bring":
            if (
                not isinstance(bring_items, list)
                or not 1 <= len(bring_items) <= 8
                or any(
                    not isinstance(item, str)
                    or not item
                    or len(item) > 80
                    or "\n" in item
                    or "\r" in item
                    or len(item.split()) > 8
                    for item in bring_items
                )
            ):
                raise ValueError("Message bring items are not privacy-safe")
            if requirement not in {"required", "recommended"}:
                raise ValueError("Invalid message bring requirement")
        elif bring_items or requirement is not None:
            raise ValueError("Bring fields are only valid for bring facts")
        if not 0 <= float(fact["confidence"]) <= 1:
            raise ValueError("Message event confidence must be between zero and one")

    def message_event_items(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT fact.*,
                   workflow.first_delivered_at,
                   workflow.last_delivered_at,
                   workflow.acknowledged_at,
                   workflow.completed_at,
                   workflow.dismissed_at
            FROM message_event_facts fact
            LEFT JOIN item_workflow workflow
              ON workflow.stable_key = fact.stable_key
            WHERE fact.workflow_state IN ('active', 'cancelled')
            ORDER BY fact.effective_date, fact.child, fact.id
            """
        ).fetchall()
        try:
            latest_run_id = self.latest_completed_run_id()
        except ValueError:
            latest_run_id = 0
        result: list[dict[str, Any]] = []
        for row in rows:
            event_kind = str(row["event_kind"])
            participants = json.loads(row["participant_display_json"] or "[]")
            bring_items = json.loads(row["bring_items_json"] or "[]")
            activity_kind = None
            if event_kind == "bring":
                if len(bring_items) == 1:
                    item_text = bring_items[0]
                else:
                    item_text = ", ".join(bring_items[:-1]) + f" og {bring_items[-1]}"
                title = f"Medbring {item_text}"
                requirement = str(row["requirement"] or "required")
                summary = _sentence(
                    ("Skal med" if requirement == "required" else "Må gerne tages med")
                    + f" {danish_short_date(row['effective_date'])}"
                )
                if participants:
                    summary += " " + _sentence(f"Fra {', '.join(participants)}")
            else:
                requirement = None
                # Read-only stores opened before the column migration lack it.
                activity_kind = (
                    row["activity_kind"]
                    if event_kind == "event" and "activity_kind" in row.keys()
                    else None
                )
                title = MESSAGE_ACTIVITY_TITLES.get(activity_kind) or MESSAGE_EVENT_TITLES[event_kind]
                summary = (
                    _sentence(f"Sted: {row['necessary_location']}")
                    if row["necessary_location"]
                    else "Se beskeden i SkoleIntra for detaljer."
                )
            result.append(
                {
                    "id": -int(row["id"]),
                    "stable_key": row["stable_key"],
                    "stream": "actions" if event_kind == "bring" else "calendar",
                    "item_type": (
                        "bring"
                        if event_kind == "bring"
                        else "event"
                        if event_kind == "event"
                        else "schedule_change"
                    ),
                    "audience": "child",
                    "child": row["child"],
                    "title": title,
                    "summary": summary,
                    "effective_date": row["effective_date"],
                    "end_date": row["end_date"],
                    "all_day": bool(row["all_day"]),
                    "timing_rule": (
                        "needed_on_source_day" if event_kind == "bring" else "explicit"
                    ),
                    "actionability": requirement or "informational",
                    "urgency": (
                        "attention"
                        if event_kind != "bring" or requirement == "required"
                        else "routine"
                    ),
                    "urgency_reason": "",
                    "confidence": float(row["confidence"]),
                    "needs_review": bool(row["needs_review"]),
                    "review_reason": (
                        "Tjek beskeden i SkoleIntra: tid eller omfang er ikke afklaret."
                        if row["needs_review"]
                        else ""
                    ),
                    "state": (
                        "cancelled"
                        if event_kind == "cancellation"
                        or row["cancellation_state"] == "cancelled"
                        else "active"
                    ),
                    "source_urls": [],
                    "source_types": ["messages"],
                    "harvest_run_id": latest_run_id,
                    "first_delivered_at": row["first_delivered_at"],
                    "last_delivered_at": row["last_delivered_at"],
                    "acknowledged_at": row["acknowledged_at"],
                    "completed_at": row["completed_at"],
                    "dismissed_at": row["dismissed_at"],
                    "activity_kind": activity_kind,
                    "message_thread_ref": row["thread_ref"],
                    "privacy_policy_version": row["policy_version"],
                }
            )
        return result

    def message_processing_receipt(
        self,
        *,
        thread_ref: str,
        message_ref: str,
        extractor_version: str | None = None,
        policy_version: str | None = None,
    ) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM message_processing_receipts "
            "WHERE thread_ref = ? AND message_ref = ?",
            (thread_ref, message_ref),
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        if (
            extractor_version is not None
            and result["extractor_version"] != extractor_version
        ):
            return None
        if policy_version is not None and result["policy_version"] != policy_version:
            return None
        return result

    def record_message_processing_attempt(
        self,
        *,
        thread_ref: str,
        message_ref: str,
        status: str,
        stage: str,
        error_code: str | None = None,
        response_id: str | None = None,
        http_status: int | None = None,
        usage: dict[str, Any] | None = None,
    ) -> None:
        if not re.fullmatch(r"thr_[a-z2-7]{16}", thread_ref):
            raise ValueError("Invalid opaque thread reference")
        if not re.fullmatch(r"msg_[a-z2-7]{16}", message_ref):
            raise ValueError("Invalid opaque message reference")
        if status not in {"succeeded", "failed"}:
            raise ValueError("Invalid message processing attempt status")
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", stage):
            raise ValueError("Invalid message processing attempt stage")
        if error_code is not None and not re.fullmatch(
            r"[a-z0-9][a-z0-9_]{0,95}", error_code
        ):
            raise ValueError("Invalid message processing error code")
        if response_id is not None and (
            len(response_id) > 200 or "\n" in response_id or "\r" in response_id
        ):
            raise ValueError("Invalid provider response identifier")
        if http_status is not None and not 100 <= int(http_status) <= 599:
            raise ValueError("Invalid provider HTTP status")
        safe_usage = usage or {}
        prompt_tokens = max(0, int(safe_usage.get("prompt_tokens") or 0))
        completion_tokens = max(0, int(safe_usage.get("completion_tokens") or 0))
        total_tokens = max(0, int(safe_usage.get("total_tokens") or 0))
        cost = max(0.0, float(safe_usage.get("cost") or 0.0))
        self.connection.execute(
            """
            INSERT INTO message_processing_attempts(
                thread_ref, message_ref, status, stage, error_code,
                response_id, http_status, prompt_tokens, completion_tokens,
                total_tokens, cost, attempted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(thread_ref, message_ref) DO UPDATE SET
                status = excluded.status,
                stage = excluded.stage,
                error_code = excluded.error_code,
                response_id = excluded.response_id,
                http_status = excluded.http_status,
                prompt_tokens = excluded.prompt_tokens,
                completion_tokens = excluded.completion_tokens,
                total_tokens = excluded.total_tokens,
                cost = excluded.cost,
                attempted_at = excluded.attempted_at
            """,
            (
                thread_ref,
                message_ref,
                status,
                stage,
                error_code,
                response_id,
                http_status,
                prompt_tokens,
                completion_tokens,
                total_tokens,
                cost,
                utc_now(),
            ),
        )
        self.connection.commit()

    def message_processing_attempt(
        self, *, thread_ref: str, message_ref: str
    ) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM message_processing_attempts "
            "WHERE thread_ref = ? AND message_ref = ?",
            (thread_ref, message_ref),
        ).fetchone()
        return dict(row) if row is not None else None

    def message_coverage(self, *, mode: str, processor: str) -> dict[str, Any]:
        if mode == "off":
            return {
                "mode": "off",
                "processor": "disabled",
                "checked": False,
                "thread_count": 0,
                "unread_count": 0,
                "new_thread_count": 0,
                "new_count": 0,
                "unretrieved_count": 0,
                "bodies_retained": 0,
            }
        row = self.connection.execute(
            """
            SELECT COUNT(*) AS thread_count,
                   COALESCE(SUM(server_unread), 0) AS unread_count,
                   COALESCE(SUM(is_new_thread), 0) AS new_thread_count,
                   COALESCE(SUM(has_new_message), 0) AS new_count,
                   COALESCE(SUM(latest_unretrieved), 0) AS unretrieved_count
            FROM message_thread_state
            WHERE exists_now = 1
            """
        ).fetchone()
        assert row is not None
        return {
            "mode": mode,
            "processor": processor,
            "checked": True,
            "thread_count": int(row["thread_count"]),
            "unread_count": int(row["unread_count"]),
            "new_thread_count": int(row["new_thread_count"]),
            "new_count": int(row["new_count"]),
            "unretrieved_count": int(row["unretrieved_count"]),
            "bodies_retained": 0 if mode == "events-only" else None,
        }

    def record_message_event_feedback(
        self,
        *,
        thread_ref: str,
        verdict: str,
        stable_key: str | None = None,
    ) -> dict[str, Any]:
        allowed = {
            "correct",
            "wrong_date",
            "wrong_time",
            "wrong_location",
            "not_event",
            "missed_event",
            "duplicate",
            "too_much_detail",
            "needed_source_open",
        }
        if verdict not in allowed:
            raise ValueError("Unsupported message event feedback verdict")
        if not re.fullmatch(r"thr_[a-z2-7]{16}", thread_ref):
            raise ValueError("Invalid opaque thread reference")
        if stable_key is not None and not re.fullmatch(
            r"message-event:[a-f0-9]{64}", stable_key
        ):
            raise ValueError("Invalid message event stable key")
        now = utc_now()
        cursor = self.connection.execute(
            """
            INSERT INTO message_event_feedback(
                thread_ref, stable_key, verdict, recorded_at
            ) VALUES (?, ?, ?, ?)
            """,
            (thread_ref, stable_key, verdict, now),
        )
        self.connection.commit()
        return {
            "feedback_id": int(cursor.lastrowid),
            "thread_ref": thread_ref,
            "stable_key": stable_key,
            "verdict": verdict,
            "recorded_at": now,
        }

    def message_evaluation_report(self) -> dict[str, Any]:
        rows = self.connection.execute(
            """
            SELECT verdict, COUNT(*) AS count
            FROM message_event_feedback
            GROUP BY verdict ORDER BY verdict
            """
        ).fetchall()
        counts = {str(row["verdict"]): int(row["count"]) for row in rows}
        total = sum(counts.values())
        reviewed = total - counts.get("missed_event", 0)
        correct = counts.get("correct", 0)
        return {
            "feedback_count": total,
            "verdict_counts": counts,
            "reviewed_fact_count": reviewed,
            "observed_missed_event_count": counts.get("missed_event", 0),
            "observed_precision": (
                round(correct / reviewed, 4) if reviewed else None
            ),
        }
