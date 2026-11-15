from __future__ import annotations

import hashlib
import hmac
import json
import re
import shlex
import subprocess
import time
import unicodedata
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from typing import Any, Callable

import httpx
from jsonschema import Draft202012Validator, FormatChecker

from skoleklar.triage.derivation import (
    OpenRouterClient,
    OpenRouterGenerationError,
    OpenRouterHTTPError,
)
from skolekit.privacy import normalize_latest_activity, shorten_name, shorten_participants
from skoleklar.restricted_messages import MESSAGE_POLICY_VERSION, EphemeralThread, message_handle
from skoleklar.triage.prompt_manifest import DEFAULT_SLOTS, load_prompt_manifest


MESSAGE_EVENT_MANIFEST_ID = "message-events-v3"
_SCHEMA_VALIDATOR = Draft202012Validator(
    load_prompt_manifest(MESSAGE_EVENT_MANIFEST_ID, slots=DEFAULT_SLOTS).output_schema,
    format_checker=FormatChecker(),
)


def message_event_manifest():
    """The extractor's manifest with the household's names in its slots."""
    return load_prompt_manifest(MESSAGE_EVENT_MANIFEST_ID)


def message_event_extractor_version() -> str:
    """`message-events-v3:<hash>`: stored with each fact, so a changed prompt re-extracts."""
    manifest = message_event_manifest()
    return f"{manifest.manifest_id}:{manifest.content_hash[:16]}"


class MessageEventExtractionError(RuntimeError):
    """A deliberately content-free event extraction failure."""


def _normalized(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).translate(
        str.maketrans(
            {
                "\u2018": "'",
                "\u2019": "'",
                "\u201c": '"',
                "\u201d": '"',
                "\u2013": "-",
                "\u2014": "-",
            }
        )
    )
    return re.sub(r"\s+", " ", normalized).strip().casefold()


def _words_only(value: str) -> str:
    return re.sub(r"[^\wÆØÅæøå]+", " ", _normalized(value)).strip()


def _latest_inbox_message(thread: EphemeralThread) -> dict[str, Any] | None:
    eligible = [
        message
        for message in thread.messages
        if not message.get("is_outbox") and not message.get("is_copied_to_archive")
    ]
    return eligible[-1] if eligible else None


def event_document(
    thread: EphemeralThread,
    message: dict[str, Any],
    *,
    observed_on: date | None = None,
) -> dict[str, Any]:
    observed_on = observed_on or date.today()
    activity = normalize_latest_activity(
        message.get("sent_received_date_text"), observed_on=observed_on
    )
    message_date = activity[:10] if activity else observed_on.isoformat()
    return {
        "source_type": "private_message",
        "child": thread.child,
        "message_date": message_date,
        "observed_date": observed_on.isoformat(),
        "subject": message.get("subject") or "",
        "sender": shorten_name(message.get("sender")),
        "body_text": message.get("body_text") or "",
    }


def _validate_evidence(item: dict[str, Any], document: dict[str, Any]) -> None:
    quote = _normalized(str(item.get("evidence_quote") or ""))
    haystack = _normalized(
        f"{document.get('subject') or ''}\n{document.get('body_text') or ''}"
    )
    quote_words = _words_only(quote)
    words_match = bool(quote_words) and quote_words in _words_only(haystack)
    if not quote or (
        quote not in haystack and not words_match
    ):
        raise MessageEventExtractionError("event_evidence_invalid")


def _surname_tokens(thread: EphemeralThread, message: dict[str, Any]) -> set[str]:
    values = [
        str(thread.conversation.get("participants_text") or ""),
        str(message.get("sender") or ""),
    ]
    result: set[str] = set()
    for value in values:
        for participant in re.split(r"[,;]", value):
            cleaned = re.sub(r"\s*\([^)]*\)\s*$", "", participant).strip()
            tokens = cleaned.split()
            if len(tokens) > 1:
                result.add(tokens[-1].casefold())
    return result


def _safe_location(value: Any, *, prohibited_surnames: set[str]) -> str | None:
    if value is None:
        return None
    location = re.sub(r"\s+", " ", str(value)).strip()
    if not location:
        return None
    words = re.findall(r"[\wÆØÅæøå-]+", location, flags=re.UNICODE)
    if len(location) > 120 or len(words) > 8:
        return None
    if any(word.casefold() in prohibited_surnames for word in words):
        return None
    normalized = location.casefold()
    generic_locations = (
        (("bibliotek", "library"), "Library"),
        (("gymnastiksal", "sportshal", "sports hall", "gym"), "Sports hall"),
        (("klasselokale", "classroom"), "Classroom"),
        (("svømmehal", "swimming pool", "pool"), "Swimming pool"),
        (("kirke", "church"), "Church"),
        (("museum",), "Museum"),
        (("park",), "Park"),
        (("skole", "school"), "School"),
    )
    for markers, label in generic_locations:
        if any(marker in normalized for marker in markers):
            return label
    return None


def _safe_bring_items(
    value: Any, *, prohibited_surnames: set[str]
) -> list[str]:
    if not isinstance(value, list) or not 1 <= len(value) <= 8:
        raise MessageEventExtractionError("bring_items_invalid")
    result: list[str] = []
    seen: set[str] = set()
    for raw_item in value:
        if not isinstance(raw_item, str) or "\n" in raw_item or "\r" in raw_item:
            raise MessageEventExtractionError("bring_items_invalid")
        item = re.sub(r"\s+", " ", raw_item).strip(" \t,;:.-")
        words = re.findall(r"[\wÆØÅæøå-]+", item, flags=re.UNICODE)
        if not item or len(item) > 80 or len(words) > 8:
            raise MessageEventExtractionError("bring_items_invalid")
        if any(word.casefold() in prohibited_surnames for word in words):
            raise MessageEventExtractionError("bring_items_private_name")
        normalized = item.casefold()
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append(item)
    if not result:
        raise MessageEventExtractionError("bring_items_invalid")
    return result


def project_event_facts(
    output: dict[str, Any],
    *,
    thread: EphemeralThread,
    message: dict[str, Any],
    key: bytes,
) -> list[dict[str, Any]]:
    errors = sorted(
        _SCHEMA_VALIDATOR.iter_errors(output), key=lambda item: list(item.path)
    )
    if errors:
        raise MessageEventExtractionError("event_schema_invalid")
    document = event_document(thread, message)
    prohibited_surnames = _surname_tokens(thread, message)
    participants = shorten_participants(
        thread.conversation.get("participants_text")
    )
    facts: list[dict[str, Any]] = []
    for item in output["items"]:
        _validate_evidence(item, document)
        try:
            effective = date.fromisoformat(item["effective_date"])
            end = (
                date.fromisoformat(item["end_date"])
                if item.get("end_date")
                else None
            )
        except ValueError as error:
            raise MessageEventExtractionError("event_date_invalid") from error
        if end is not None and end < effective:
            raise MessageEventExtractionError("event_date_range_invalid")
        stable_material = (
            f"{thread.thread_ref}\0{item['event_kind']}\0{item['dedupe_hint']}"
        ).encode("utf-8")
        stable_key = "message-event:" + hmac.new(
            key, stable_material, hashlib.sha256
        ).hexdigest()
        confidence = float(item["confidence"])
        event_kind = str(item["event_kind"])
        raw_location = item.get("necessary_location")
        safe_location = _safe_location(
            raw_location,
            prohibited_surnames=prohibited_surnames,
        )
        raw_bring_items = item.get("bring_items")
        bring_items = (
            _safe_bring_items(
                raw_bring_items,
                prohibited_surnames=prohibited_surnames,
            )
            if event_kind == "bring"
            else []
        )
        requirement = item.get("requirement") if event_kind == "bring" else None
        if event_kind == "bring" and requirement not in {"required", "recommended"}:
            raise MessageEventExtractionError("bring_requirement_invalid")
        if event_kind != "bring" and (raw_bring_items or item.get("requirement")):
            raise MessageEventExtractionError("bring_fields_on_non_bring_fact")
        activity_kind = item.get("activity_kind")
        if activity_kind is not None and event_kind != "event":
            raise MessageEventExtractionError("activity_kind_on_non_event_fact")
        facts.append(
            {
                "stable_key": stable_key,
                "child": thread.child,
                "event_kind": event_kind,
                "activity_kind": activity_kind,
                "effective_date": effective.isoformat(),
                "end_date": end.isoformat() if end else None,
                "all_day": bool(item["all_day"]),
                "cancellation_state": (
                    "cancelled"
                    if event_kind == "cancellation"
                    else item["cancellation_state"]
                ),
                "necessary_location": safe_location,
                "bring_items": bring_items,
                "requirement": requirement,
                "participant_display_names": participants,
                "template_key": f"private_{event_kind}",
                "confidence": confidence,
                "needs_review": bool(item["needs_review"])
                or confidence < 0.75
                or not bool(item["all_day"])
                or (bool(raw_location) and safe_location is None),
                "workflow_state": (
                    "cancelled" if event_kind == "cancellation" else "active"
                ),
            }
        )
    return facts


def _local_generate(document: dict[str, Any], command: str) -> dict[str, Any]:
    argv = shlex.split(command)
    if not argv:
        raise MessageEventExtractionError("local_processor_not_configured")
    try:
        result = subprocess.run(
            argv,
            input=json.dumps(document, ensure_ascii=False),
            text=True,
            capture_output=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise MessageEventExtractionError("local_processor_failed") from error
    if result.returncode != 0:
        raise MessageEventExtractionError("local_processor_failed")
    try:
        output = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise MessageEventExtractionError("local_processor_invalid_json") from error
    if not isinstance(output, dict):
        raise MessageEventExtractionError("local_processor_invalid_output")
    return output


def _process_changed_threads_sequential(
    store: Any,
    threads: list[EphemeralThread],
    *,
    key: bytes,
    processor: str,
    model: str,
    api_key: str | None = None,
    base_url: str | None = None,
    local_command: str | None = None,
    http_client: httpx.Client | None = None,
) -> dict[str, Any]:
    if processor == "disabled":
        return {
            "processor": processor,
            "status": "complete",
            "changed_threads": len(threads),
            "processed_threads": 0,
            "event_count": 0,
            "failure_count": 0,
            "attempt_count": 0,
            "retry_count": 0,
            "failure_reasons": {},
            "failures": [],
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "cost": 0.0,
            },
        }
    remote: OpenRouterClient | None = None
    if processor == "zdr":
        if not api_key:
            raise MessageEventExtractionError("zdr_processor_missing_api_key")
        kwargs: dict[str, Any] = {"api_key": api_key, "model": model}
        if base_url:
            kwargs["base_url"] = base_url
        if http_client is not None:
            kwargs["client"] = http_client
        remote = OpenRouterClient(**kwargs)
    elif processor == "local" and not local_command:
        raise MessageEventExtractionError("local_processor_not_configured")
    elif processor not in {"local", "zdr"}:
        raise MessageEventExtractionError("unsupported_message_processor")

    processed = 0
    event_count = 0
    attempt_count = 0
    retry_count = 0
    failure_details: list[dict[str, Any]] = []
    failure_reasons: Counter[str] = Counter()
    total_usage: dict[str, float | int] = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cost": 0.0,
    }

    def add_usage(usage: dict[str, Any] | None) -> None:
        value = usage or {}
        for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
            total_usage[field] = int(total_usage[field]) + int(value.get(field) or 0)
        total_usage["cost"] = float(total_usage["cost"]) + float(
            value.get("cost") or 0.0
        )

    def record_attempt(
        *,
        thread_ref: str,
        message_ref: str,
        status: str,
        stage: str,
        error_code: str | None,
        response_id: str = "",
        http_status: int | None = None,
        usage: dict[str, Any] | None = None,
    ) -> None:
        store.record_message_processing_attempt(
            thread_ref=thread_ref,
            message_ref=message_ref,
            status=status,
            stage=stage,
            error_code=error_code,
            response_id=response_id or None,
            http_status=http_status,
            usage=usage,
        )

    def add_failure(
        *,
        thread_ref: str,
        message_ref: str,
        stage: str,
        error_code: str,
        response_id: str = "",
    ) -> None:
        failure_reasons[error_code] += 1
        detail = {
            "thread_ref": thread_ref,
            "message_ref": message_ref,
            "stage": stage,
            "error_code": error_code,
        }
        if response_id:
            detail["response_id"] = response_id
        failure_details.append(detail)

    for thread in threads:
        message = _latest_inbox_message(thread)
        if message is None:
            continue
        current_message_ref = message_handle(
            message, student_id=thread.student_id, key=key
        )
        if store.message_processing_receipt(
            thread_ref=thread.thread_ref,
            message_ref=current_message_ref,
            extractor_version=message_event_extractor_version(),
            policy_version=MESSAGE_POLICY_VERSION,
        ):
            continue
        document = event_document(thread, message)
        output_budget = 2500
        retried_length = False
        retried_evidence = False
        while True:
            response_id = ""
            response_usage: dict[str, Any] = {}
            try:
                attempt_count += 1
                if processor == "zdr":
                    assert remote is not None
                    try:
                        response = remote.generate(
                            document,
                            manifest=message_event_manifest(),
                            max_output_tokens=output_budget,
                        )
                    except OpenRouterGenerationError as error:
                        add_usage(error.usage)
                        record_attempt(
                            thread_ref=thread.thread_ref,
                            message_ref=current_message_ref,
                            status="failed",
                            stage=error.stage,
                            error_code=error.error_code,
                            response_id=error.response_id,
                            http_status=200,
                            usage=error.usage,
                        )
                        if error.error_code == "max_output_tokens" and not retried_length:
                            retried_length = True
                            retry_count += 1
                            output_budget = 5000
                            continue
                        add_failure(
                            thread_ref=thread.thread_ref,
                            message_ref=current_message_ref,
                            stage=error.stage,
                            error_code=error.error_code,
                            response_id=error.response_id,
                        )
                        break
                    except OpenRouterHTTPError as error:
                        error_code = f"http_{error.status_code}"
                        record_attempt(
                            thread_ref=thread.thread_ref,
                            message_ref=current_message_ref,
                            status="failed",
                            stage="provider_http",
                            error_code=error_code,
                            http_status=error.status_code,
                        )
                        add_failure(
                            thread_ref=thread.thread_ref,
                            message_ref=current_message_ref,
                            stage="provider_http",
                            error_code=error_code,
                        )
                        break
                    except httpx.TimeoutException:
                        record_attempt(
                            thread_ref=thread.thread_ref,
                            message_ref=current_message_ref,
                            status="failed",
                            stage="provider_transport",
                            error_code="timeout",
                        )
                        add_failure(
                            thread_ref=thread.thread_ref,
                            message_ref=current_message_ref,
                            stage="provider_transport",
                            error_code="timeout",
                        )
                        break
                    except httpx.HTTPError:
                        record_attempt(
                            thread_ref=thread.thread_ref,
                            message_ref=current_message_ref,
                            status="failed",
                            stage="provider_transport",
                            error_code="http_transport_error",
                        )
                        add_failure(
                            thread_ref=thread.thread_ref,
                            message_ref=current_message_ref,
                            stage="provider_transport",
                            error_code="http_transport_error",
                        )
                        break
                    response_id = str(response.get("response_id") or "")
                    response_usage = response.get("usage") or {}
                    add_usage(response_usage)
                    output = response["output"]
                else:
                    assert local_command is not None
                    output = _local_generate(document, local_command)

                try:
                    facts = project_event_facts(
                        output,
                        thread=thread,
                        message=message,
                        key=key,
                    )
                except MessageEventExtractionError as error:
                    error_code = str(error)
                    record_attempt(
                        thread_ref=thread.thread_ref,
                        message_ref=current_message_ref,
                        status="failed",
                        stage="projection",
                        error_code=error_code,
                        response_id=response_id,
                        http_status=200 if processor == "zdr" else None,
                        usage=response_usage,
                    )
                    if error_code == "event_evidence_invalid" and not retried_evidence:
                        retried_evidence = True
                        retry_count += 1
                        document = {
                            **document,
                            "validation_feedback": (
                                "Copy evidence_quote as one exact contiguous substring "
                                "from subject or body_text. Do not paraphrase or combine text."
                            ),
                        }
                        continue
                    add_failure(
                        thread_ref=thread.thread_ref,
                        message_ref=current_message_ref,
                        stage="projection",
                        error_code=error_code,
                        response_id=response_id,
                    )
                    break

                try:
                    store.complete_message_event_extraction(
                        thread_ref=thread.thread_ref,
                        message_ref=current_message_ref,
                        facts=facts,
                        extractor_version=message_event_extractor_version(),
                        policy_version=MESSAGE_POLICY_VERSION,
                    )
                except ValueError:
                    record_attempt(
                        thread_ref=thread.thread_ref,
                        message_ref=current_message_ref,
                        status="failed",
                        stage="storage_validation",
                        error_code="storage_validation_rejected",
                        response_id=response_id,
                        http_status=200 if processor == "zdr" else None,
                        usage=response_usage,
                    )
                    add_failure(
                        thread_ref=thread.thread_ref,
                        message_ref=current_message_ref,
                        stage="storage_validation",
                        error_code="storage_validation_rejected",
                        response_id=response_id,
                    )
                    break

                record_attempt(
                    thread_ref=thread.thread_ref,
                    message_ref=current_message_ref,
                    status="succeeded",
                    stage="persisted",
                    error_code=None,
                    response_id=response_id,
                    http_status=200 if processor == "zdr" else None,
                    usage=response_usage,
                )
                processed += 1
                event_count += len(facts)
                break
            except MessageEventExtractionError as error:
                error_code = str(error)
                record_attempt(
                    thread_ref=thread.thread_ref,
                    message_ref=current_message_ref,
                    status="failed",
                    stage="local_processor",
                    error_code=error_code,
                )
                add_failure(
                    thread_ref=thread.thread_ref,
                    message_ref=current_message_ref,
                    stage="local_processor",
                    error_code=error_code,
                )
                break
            except Exception:
                record_attempt(
                    thread_ref=thread.thread_ref,
                    message_ref=current_message_ref,
                    status="failed",
                    stage="internal",
                    error_code="internal_error",
                    response_id=response_id,
                    http_status=200 if processor == "zdr" and response_id else None,
                    usage=response_usage,
                )
                add_failure(
                    thread_ref=thread.thread_ref,
                    message_ref=current_message_ref,
                    stage="internal",
                    error_code="internal_error",
                    response_id=response_id,
                )
                break
    failures = len(failure_details)
    return {
        "processor": processor,
        "status": "complete" if failures == 0 else "partial",
        "changed_threads": len(threads),
        "processed_threads": processed,
        "event_count": event_count,
        "failure_count": failures,
        "attempt_count": attempt_count,
        "retry_count": retry_count,
        "failure_reasons": dict(sorted(failure_reasons.items())),
        "failures": failure_details,
        "usage": total_usage,
    }


class _DeferredMessageEventStore:
    """Collect one worker's safe projection without touching SQLite."""

    def __init__(self) -> None:
        self.facts: list[dict[str, Any]] | None = None
        self.attempts: list[dict[str, Any]] = []

    def message_processing_receipt(self, **_kwargs: Any) -> None:
        return None

    def complete_message_event_extraction(
        self, *, facts: list[dict[str, Any]], **_kwargs: Any
    ) -> None:
        self.facts = facts

    def record_message_processing_attempt(self, **kwargs: Any) -> None:
        self.attempts.append(kwargs)


def process_changed_threads(
    store: Any,
    threads: list[EphemeralThread],
    *,
    key: bytes,
    processor: str,
    model: str,
    api_key: str | None = None,
    base_url: str | None = None,
    local_command: str | None = None,
    max_concurrency: int = 2,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Process private messages concurrently while serializing durable writes."""

    started = time.monotonic()
    if processor == "disabled":
        result = _process_changed_threads_sequential(
            store,
            threads,
            key=key,
            processor=processor,
            model=model,
            api_key=api_key,
            base_url=base_url,
            local_command=local_command,
        )
        return {**result, "concurrency": 0, "duration_ms": 0}
    if processor == "zdr" and not api_key:
        raise MessageEventExtractionError("zdr_processor_missing_api_key")
    if processor == "local" and not local_command:
        raise MessageEventExtractionError("local_processor_not_configured")
    if processor not in {"local", "zdr"}:
        raise MessageEventExtractionError("unsupported_message_processor")

    pending: list[EphemeralThread] = []
    for thread in threads:
        message = _latest_inbox_message(thread)
        if message is None:
            continue
        message_ref = message_handle(message, student_id=thread.student_id, key=key)
        if store.message_processing_receipt(
            thread_ref=thread.thread_ref,
            message_ref=message_ref,
            extractor_version=message_event_extractor_version(),
            policy_version=MESSAGE_POLICY_VERSION,
        ):
            continue
        pending.append(thread)

    worker_count = (
        min(max(1, int(max_concurrency)), 4, len(pending))
        if processor == "zdr" and pending
        else min(1, len(pending))
    )
    shared_client: httpx.Client | None = None
    if processor == "zdr" and pending:
        shared_client = httpx.Client(
            timeout=120.0,
            limits=httpx.Limits(
                max_connections=max(2, worker_count),
                max_keepalive_connections=max(2, worker_count),
            ),
        )

    def run_one(
        thread: EphemeralThread,
    ) -> tuple[dict[str, Any], _DeferredMessageEventStore]:
        deferred = _DeferredMessageEventStore()
        result = _process_changed_threads_sequential(
            deferred,
            [thread],
            key=key,
            processor=processor,
            model=model,
            api_key=api_key,
            base_url=base_url,
            local_command=local_command,
            http_client=shared_client,
        )
        return result, deferred

    worker_results: list[tuple[dict[str, Any], _DeferredMessageEventStore]] = []
    progress_completed = 0
    progress_retries = 0
    progress_failures = 0

    def report_worker(summary: dict[str, Any]) -> None:
        nonlocal progress_completed, progress_retries, progress_failures
        progress_completed += 1
        progress_retries += int(summary.get("retry_count") or 0)
        progress_failures += int(summary.get("failure_count") or 0)
        if progress_callback is None:
            return
        elapsed_ms = round((time.monotonic() - started) * 1000)
        remaining = len(pending) - progress_completed
        progress_callback(
            {
                "completed": progress_completed,
                "total": len(pending),
                "retry_count": progress_retries,
                "failure_count": progress_failures,
                "elapsed_ms": elapsed_ms,
                "eta_ms": (
                    round(elapsed_ms / progress_completed * remaining)
                    if progress_completed and remaining
                    else 0
                ),
            }
        )

    try:
        if worker_count > 1:
            with ThreadPoolExecutor(
                max_workers=worker_count,
                thread_name_prefix="message-zdr",
            ) as executor:
                futures = [executor.submit(run_one, thread) for thread in pending]
                for future in as_completed(futures):
                    worker_result = future.result()
                    worker_results.append(worker_result)
                    report_worker(worker_result[0])
        else:
            for thread in pending:
                worker_result = run_one(thread)
                worker_results.append(worker_result)
                report_worker(worker_result[0])
    finally:
        if shared_client is not None:
            shared_client.close()

    processed = 0
    event_count = 0
    attempt_count = 0
    retry_count = 0
    failures: list[dict[str, Any]] = []
    failure_reasons: Counter[str] = Counter()
    total_usage: dict[str, float | int] = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cost": 0.0,
    }

    for worker_summary, deferred in worker_results:
        attempt_count += int(worker_summary["attempt_count"])
        retry_count += int(worker_summary["retry_count"])
        usage = worker_summary.get("usage") or {}
        for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
            total_usage[field] = int(total_usage[field]) + int(usage.get(field) or 0)
        total_usage["cost"] = float(total_usage["cost"]) + float(
            usage.get("cost") or 0.0
        )

        final_attempt = dict(deferred.attempts[-1]) if deferred.attempts else {}
        if worker_summary["status"] == "complete" and deferred.facts is not None:
            try:
                store.complete_message_event_extraction(
                    thread_ref=final_attempt["thread_ref"],
                    message_ref=final_attempt["message_ref"],
                    facts=deferred.facts,
                    extractor_version=message_event_extractor_version(),
                    policy_version=MESSAGE_POLICY_VERSION,
                )
                final_attempt.update(
                    status="succeeded", stage="persisted", error_code=None
                )
                processed += 1
                event_count += len(deferred.facts)
            except ValueError:
                final_attempt.update(
                    status="failed",
                    stage="storage_validation",
                    error_code="storage_validation_rejected",
                )
                worker_summary = {
                    **worker_summary,
                    "status": "partial",
                    "failure_count": 1,
                    "failure_reasons": {"storage_validation_rejected": 1},
                    "failures": [
                        {
                            "thread_ref": final_attempt["thread_ref"],
                            "message_ref": final_attempt["message_ref"],
                            "stage": "storage_validation",
                            "error_code": "storage_validation_rejected",
                        }
                    ],
                }

        if final_attempt:
            final_attempt["usage"] = usage
            store.record_message_processing_attempt(**final_attempt)
        failures.extend(worker_summary.get("failures") or [])
        failure_reasons.update(worker_summary.get("failure_reasons") or {})

    failure_count = len(failures)
    return {
        "processor": processor,
        "status": "complete" if failure_count == 0 else "partial",
        "changed_threads": len(threads),
        "processed_threads": processed,
        "event_count": event_count,
        "failure_count": failure_count,
        "attempt_count": attempt_count,
        "retry_count": retry_count,
        "failure_reasons": dict(sorted(failure_reasons.items())),
        "failures": failures,
        "usage": total_usage,
        "concurrency": worker_count,
        "duration_ms": round((time.monotonic() - started) * 1000),
    }
