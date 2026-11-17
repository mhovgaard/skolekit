"""The scan's stages as services: plain functions with typed requests and results.

`harvest`, `derive`, `archive`, `consolidate` and `manifest` take typed
requests and return `StageResult`. CLI handlers format results; runtimes read
fields directly, without argparse objects or parsed stdout.

The bodies are the former `command_*` functions, moved unchanged apart from
reading a request and returning a result.

"""
from __future__ import annotations

import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from time import monotonic
from typing import Any
from zoneinfo import ZoneInfo

from skoleklar import foundation
from skoleklar.triage.archival import (
    archival_manifest,
    ARCHIVAL_POLICY_VERSION,
    OpenRouterArchivalClient,
    archival_document,
    materialize_agent_decisions,
    plan_archival,
)
from skolekit.auth.browser import browser_login
from skoleklar.harvest import HarvestPolicy, collect_all
from skoleklar.triage.consolidation import (
    consolidation_manifest,
    CONSOLIDATION_VERSION,
    OpenRouterConsolidationClient,
    consolidation_cache_key,
    consolidation_document,
    materialize_consolidation,
    repair_consolidation_output,
)
from skoleklar.settings import openrouter_api_key
from skoleklar.delivery_manifest import (
    build_delivery_manifest,
    delivery_items_from_snapshot,
    publish_manifest,
)
from skoleklar.delivery_store import GCSDeliveryStore, LocalDeliveryStore
from skoleklar.triage.derivation import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    derivation_manifest,
    PROMPT_VERSION,
    PROVIDER,
    ROUTINE_CALENDAR_FILTER_VERSION,
    TAXONOMY_VERSION,
    OpenRouterClient,
    OpenRouterHTTPError,
    candidate_document,
    derivation_cache_key,
    deterministic_calendar_items,
    deterministic_derivation_cache_key,
    validate_items,
)
from skoleklar.triage.message_events import (
    message_event_extractor_version,
    MessageEventExtractionError,
    process_changed_threads,
)
from skolekit.privacy import MessagePrivacyError
from skoleklar.restricted_messages import (
    MESSAGE_POLICY_VERSION,
    collect_restricted_messages,
    load_or_create_handle_key,
    resolve_message_mode,
    resolve_message_processor,
)
from skoleklar.progress import _elapsed_text, _emit_progress
from skolekit.records import SourceCheck
from skolekit.auth.session import (
    SessionStore,
    school_client,
    session_is_valid,
)
from skolekit.state import state_dir
from skoleklar.household import load_site_profile, snapshot_db_path
from skoleklar.meaning import Meaning, load_meaning
from skolekit.profile import ProfileError, SiteProfile
from skoleklar.storage import SnapshotStore


@dataclass(frozen=True)
class StageResult:
    """What a stage did: its exit code (0 ok, 2 refused or failed) and its JSON report."""

    exit_code: int
    output: dict[str, Any] | None = None

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


@dataclass(frozen=True)
class HarvestRequest:
    db: str | Path | None = None
    mode: str = "refresh"
    calendar_past_weeks: int | None = None
    calendar_future_weeks: int | None = None
    weekly_plan_weeks: int | None = None
    message_pages: int | None = None
    frontpage_pages: int | None = None
    auto_login: bool = False
    auto_login_timeout: int = 2
    message_mode: str | None = None
    message_processor: str | None = None
    model: str = DEFAULT_MODEL
    message_concurrency: int = 2
    progress: str = "auto"


@dataclass(frozen=True)
class DeriveRequest:
    db: str | Path | None = None
    run_id: int | None = None
    source: Sequence[str] | None = None
    limit: int | None = None
    dry_run: bool = False
    model: str = DEFAULT_MODEL
    backfill_since: str | None = None
    message_mode: str | None = None
    # Also re-derive the sources whose facts on the board were made under an older
    # contract (skoleklar.fact), from `as_of` (default today) through the board's window.
    migrate: bool = False
    as_of: str | None = None
    migrate_limit: int = 40


@dataclass(frozen=True)
class ArchiveRequest:
    as_of: str
    db: str | Path | None = None
    model: str = DEFAULT_MODEL
    dry_run: bool = False
    no_agent_review: bool = False
    message_mode: str | None = None


@dataclass(frozen=True)
class ConsolidateRequest:
    as_of: str
    db: str | Path | None = None
    limit: int = 100
    model: str = DEFAULT_MODEL
    dry_run: bool = False
    message_mode: str | None = None


@dataclass(frozen=True)
class ManifestRequest:
    date: str
    db: str | Path | None = None
    delivery_root: str | None = None
    delivery_bucket: str | None = None
    dry_run: bool = False
    message_mode: str | None = None
    message_processor: str | None = None


def load_profile_or_report() -> SiteProfile | None:
    """The household's profile, or None after printing why it can't be read."""
    try:
        return load_site_profile()
    except ProfileError as error:
        print(f"Invalid profile: {error}", file=sys.stderr)
        return None


def load_meaning_or_report() -> Meaning | None:
    """The household's [meaning], or None after printing why it can't be read."""
    try:
        return load_meaning(state_dir())
    except ProfileError as error:
        print(f"Invalid household config: {error}", file=sys.stderr)
        return None


async def harvest(request: HarvestRequest) -> StageResult:
    pipeline_started = monotonic()
    timings_ms: dict[str, int] = {}
    try:
        message_mode = resolve_message_mode(request.message_mode)
        message_processor = resolve_message_processor(
            request.message_processor
        )
        policy = HarvestPolicy.for_mode(
            request.mode,
            calendar_past_weeks=request.calendar_past_weeks,
            calendar_future_weeks=request.calendar_future_weeks,
            weekly_plan_weeks=request.weekly_plan_weeks,
            message_pages=request.message_pages,
            frontpage_pages=request.frontpage_pages,
            message_mode=message_mode,
            message_processor=message_processor,
        )
    except ValueError as error:
        print(f"Invalid harvest policy: {error}", file=sys.stderr)
        return StageResult(2)
    profile = load_profile_or_report()
    meaning = load_meaning_or_report() if profile is not None else None
    if profile is None or meaning is None:
        return StageResult(2)
    database = (
        Path(request.db).expanduser().resolve()
        if request.db
        else snapshot_db_path()
    )
    handle_key: bytes | None = None
    if message_mode == "events-only":
        try:
            handle_key = load_or_create_handle_key()
        except MessagePrivacyError as error:
            print(str(error), file=sys.stderr)
            return StageResult(2)
    api_key: str | None = None
    if message_processor == "zdr":
        api_key = openrouter_api_key()
        if not api_key:
            print(
                "Events-only ZDR processing requires an OpenRouter API key.",
                file=sys.stderr,
            )
            return StageResult(2)
    local_command = os.environ.get("AG_SCHOOL_MESSAGE_LOCAL_COMMAND")
    if message_processor == "local" and not local_command:
        print(
            "Local message processing requires AG_SCHOOL_MESSAGE_LOCAL_COMMAND.",
            file=sys.stderr,
        )
        return StageResult(2)
    _emit_progress(request, "Authentication")
    authentication_started = monotonic()
    with SnapshotStore(
        database
    ) as store:
        restricted_previous: dict[int, dict[str, dict[str, Any]]] = {}
        if message_mode == "events-only":
            for child in profile.children:
                states = store.message_thread_states_by_ref(child.student_id)
                for value in states.values():
                    processing_message_ref = value.get(
                        "latest_inbox_message_ref"
                    )
                    value["latest_message_processed"] = bool(
                        processing_message_ref
                        and store.message_processing_receipt(
                            thread_ref=value["thread_ref"],
                            message_ref=processing_message_ref,
                            extractor_version=message_event_extractor_version(),
                            policy_version=MESSAGE_POLICY_VERSION,
                        )
                    )
                restricted_previous[child.student_id] = states
    state = SessionStore().load()
    authenticated = state is not None and session_is_valid(
        state, profile.login_url
    )
    if not authenticated:
        if not request.auto_login:
            print(
                "Saved session is missing or expired. Run `uv run skoleklar login` or use "
                "`uv run skoleklar harvest --auto-login`.",
                file=sys.stderr,
            )
            return StageResult(2)
        login_result = await browser_login(
            profile.login_url,
            timeout=request.auto_login_timeout,
            use_keychain=True,
            auto_submit=True,
            headless=True,
        )
        if login_result != 0:
            return StageResult(login_result)
        state = SessionStore().load()
        if state is None or not session_is_valid(state, profile.login_url):
            print("Automatic login did not produce a usable session.", file=sys.stderr)
            return StageResult(2)
    timings_ms["authentication"] = round(
        (monotonic() - authentication_started) * 1000
    )
    _emit_progress(
        request,
        "Authentication",
        detail=_elapsed_text(timings_ms["authentication"]),
        done=True,
    )
    restricted_collections = []
    _emit_progress(request, "School sources")
    collection_started = monotonic()
    with school_client(state, profile.school.host) as client:
        checks = collect_all(
            client,
            children=profile.children,
            policy=policy,
        )
        if message_mode == "events-only":
            assert handle_key is not None
            checks = [check for check in checks if check.source_type != "messages"]
            for child in profile.children:
                try:
                    collection = collect_restricted_messages(
                        client,
                        child,
                        key=handle_key,
                        previous_states=restricted_previous.get(
                            child.student_id, {}
                        ),
                        page_limit=policy.message_pages,
                        page_safety_limit=policy.page_safety_limit,
                        fetch_changed_content=message_processor != "disabled",
                    )
                except Exception as error:
                    collection = None
                    checks.append(
                        SourceCheck(
                            student_id=child.student_id,
                            child=child.child,
                            source_type="messages",
                            complete=False,
                            error=f"{type(error).__name__}: restricted inbox failed",
                            metadata={
                                "message_mode": "events-only",
                                "bodies_retained": 0,
                            },
                            coverage={"kind": "current_state"},
                        )
                    )
                if collection is not None:
                    restricted_collections.append((child, collection))
                    checks.append(collection.check)
    timings_ms["source_collection"] = round(
        (monotonic() - collection_started) * 1000
    )
    _emit_progress(
        request,
        "School sources",
        detail=(
            f"{sum(len(check.records) for check in checks)} records · "
            f"{_elapsed_text(timings_ms['source_collection'])}"
        ),
        done=True,
    )
    extraction_summary: dict[str, Any] | None = None
    storage_started = monotonic()
    with SnapshotStore(
        database
    ) as store:
        run_id = store.begin_run()
        summary = store.store_checks(run_id, checks)
        if message_mode == "events-only":
            for child, collection in restricted_collections:
                store.replace_message_thread_states(
                    student_id=child.student_id,
                    child=collection.check.child,
                    states=collection.states,
                    complete=collection.check.complete
                    and bool(collection.check.metadata.get("exhausted")),
                )
            assert handle_key is not None
            changed_threads = [
                thread
                for _, collection in restricted_collections
                for thread in collection.changed_threads
            ]
            timings_ms["source_storage"] = round(
                (monotonic() - storage_started) * 1000
            )
            _emit_progress(
                request,
                "Message index",
                detail=f"{len(changed_threads)} pending",
                done=True,
            )

            def message_progress(event: dict[str, Any]) -> None:
                detail = f"{event['completed']}/{event['total']}"
                if event["retry_count"]:
                    detail += f" · retries {event['retry_count']}"
                if event["failure_count"]:
                    detail += f" · failures {event['failure_count']}"
                detail += f" · {_elapsed_text(event['elapsed_ms'])}"
                if event["eta_ms"]:
                    detail += f" · ~{_elapsed_text(event['eta_ms'])} remaining"
                _emit_progress(request, "ZDR events", detail=detail)

            try:
                extraction_summary = process_changed_threads(
                    store,
                    changed_threads,
                    key=handle_key,
                    processor=message_processor,
                    model=request.model,
                    api_key=api_key,
                    base_url=os.environ.get("OPENROUTER_BASE_URL"),
                    local_command=local_command,
                    max_concurrency=request.message_concurrency,
                    progress_callback=message_progress,
                )
            except MessageEventExtractionError as error:
                extraction_summary = {
                    "processor": message_processor,
                    "status": "failed",
                    "error": str(error),
                    "failure_count": len(changed_threads),
                }
            timings_ms["message_processing"] = int(
                extraction_summary.get("duration_ms") or 0
            )
            _emit_progress(
                request,
                "ZDR events",
                detail=(
                    f"{extraction_summary.get('processed_threads', 0)}/"
                    f"{len(changed_threads)} processed · "
                    f"{_elapsed_text(timings_ms['message_processing'])}"
                ),
                done=not bool(extraction_summary.get("failure_count")),
            )
        else:
            timings_ms["source_storage"] = round(
                (monotonic() - storage_started) * 1000
            )
        final_status = summary["status"]
        if extraction_summary is not None and extraction_summary.get("failure_count"):
            final_status = "partial"
        summary["status"] = final_status
        store.finalize_harvest_run(run_id, status=final_status)
        # Typed sources and bookings follow the stored versions (data foundation §6).
        summary["foundation"] = foundation.refresh(
            store.connection, run_id=run_id, profile=profile, meaning=meaning,
            today=datetime.now(ZoneInfo("Europe/Copenhagen")).date())
    summary["database"] = str(database)
    summary["policy"] = {
        "mode": policy.mode,
        "calendar_past_weeks": policy.calendar_past_weeks,
        "calendar_future_weeks": policy.calendar_future_weeks,
        "weekly_plan_weeks": policy.weekly_plan_weeks,
        "message_pages": policy.message_pages,
        "frontpage_pages": policy.frontpage_pages,
        "message_mode": message_mode,
        "message_processor": message_processor,
    }
    if extraction_summary is not None:
        summary["message_events"] = extraction_summary
    timings_ms["total"] = round((monotonic() - pipeline_started) * 1000)
    summary["timings_ms"] = timings_ms
    _emit_progress(
        request,
        "Harvest complete",
        detail=_elapsed_text(timings_ms["total"]),
        done=summary["status"] == "complete",
    )
    extraction_failed = bool(
        extraction_summary and extraction_summary.get("failure_count")
    )
    return StageResult(
        0 if summary["status"] == "complete" and not extraction_failed else 2, summary
    )


def derive(request: DeriveRequest) -> StageResult:
    database = (
        Path(request.db).expanduser().resolve()
        if request.db
        else snapshot_db_path()
    )
    if not database.exists():
        print("No snapshot database. Run `uv run skoleklar harvest` first.", file=sys.stderr)
        return StageResult(2)
    try:
        resolve_message_mode(request.message_mode)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return StageResult(2)
    if request.source and "messages" in request.source:
        # Message text is never stored, so no message is a record to derive from;
        # their events come from the restricted extraction during the harvest.
        print("Messages are never stored as records, so they can't be derived.", file=sys.stderr)
        return StageResult(2)
    profile = load_profile_or_report()
    if profile is None:
        return StageResult(2)
    class_labels = profile.class_labels()
    backfill_since: date | None = None
    if request.backfill_since:
        try:
            backfill_since = date.fromisoformat(request.backfill_since)
        except ValueError:
            print(
                "--backfill-since must be an ISO date such as 2026-08-01.",
                file=sys.stderr,
            )
            return StageResult(2)
        if not request.source or request.limit is None:
            print(
                "Backfill requires at least one --source and an explicit --limit.",
                file=sys.stderr,
            )
            return StageResult(2)
    purpose = "validation" if backfill_since is not None else "live"
    if request.migrate and backfill_since is not None:
        print("--migrate re-derives live facts; it does not combine with a backfill.", file=sys.stderr)
        return StageResult(2)
    try:
        as_of = date.fromisoformat(request.as_of) if request.as_of else date.today()
    except ValueError:
        print("--as-of must be an ISO date such as 2026-11-17.", file=sys.stderr)
        return StageResult(2)
    with SnapshotStore(
        database
    ) as store:
        if backfill_since is not None:
            candidates = store.backfill_candidates(
                since=backfill_since,
                source_types=request.source,
                limit=request.limit,
            )
        else:
            candidates = store.derivation_candidates(
                request.run_id, source_types=request.source, limit=request.limit
            )
        migrated = 0
        before: dict[str, dict[str, Any]] = {}
        if request.migrate:
            # Facts the board reads must carry the current contract: re-derive the
            # rest in its window, the nearest first (docs/processing.md).
            known = {tuple(sorted(source["version_id"] for source in candidate["sources"]))
                     for candidate in candidates}
            stale = [candidate for candidate in store.contract_candidates(
                current_versions={PROMPT_VERSION, ROUTINE_CALENDAR_FILTER_VERSION},
                start=_window_start(as_of), end=as_of + timedelta(days=MIGRATION_DAYS),
                weekly_plan_keys=_plan_keys(as_of), limit=request.migrate_limit)
                if tuple(sorted(source["version_id"] for source in candidate["sources"])) not in known]
            if request.source:
                stale = [candidate for candidate in stale if candidate["source_type"] in request.source]
            candidates = [*candidates, *stale]
            migrated = len(stale)
            before = _workflow_keys(store, as_of)
        if request.dry_run:
            deterministic_count = sum(
                deterministic_calendar_items(candidate) is not None
                for candidate in candidates
            )
            summary = {
                "run_id": request.run_id or store.latest_completed_run_id(),
                "selection": "backfill" if backfill_since else "changes",
                "backfill_since": (
                    backfill_since.isoformat() if backfill_since else None
                ),
                "purpose": purpose,
                "model": request.model,
                "prompt_version": PROMPT_VERSION,
                "taxonomy_version": TAXONOMY_VERSION,
                "prompt_manifest_hash": derivation_manifest().content_hash,
                "candidate_count": len(candidates),
                "contract_candidate_count": migrated,
                "llm_candidate_count": len(candidates) - deterministic_count,
                "deterministic_candidate_count": deterministic_count,
                "candidates": [
                    {
                        "source": candidate["source_type"],
                        "source_key": candidate["source_key"],
                        "children": sorted(
                            {source["child"] for source in candidate["sources"]}
                        ),
                        "source_version_ids": [
                            source["version_id"] for source in candidate["sources"]
                        ],
                        "source_date": candidate.get("source_date"),
                        "processing": (
                            "deterministic"
                            if deterministic_calendar_items(candidate) is not None
                            else "llm"
                        ),
                    }
                    for candidate in candidates
                ],
            }
            return StageResult(0, summary)

        deterministic_count = sum(
            deterministic_calendar_items(candidate) is not None
            for candidate in candidates
        )
        client: OpenRouterClient | None = None
        if deterministic_count < len(candidates):
            api_key = openrouter_api_key()
            if not api_key:
                print(
                    "No OpenRouter API key. Set OPENROUTER_API_KEY or run "
                    "`uv run skoleklar llm-credentials set`.",
                    file=sys.stderr,
                )
                return StageResult(2)
            client = OpenRouterClient(
                api_key=api_key,
                model=request.model,
                base_url=os.environ.get("OPENROUTER_BASE_URL", DEFAULT_BASE_URL),
            )
        results: list[dict[str, Any]] = []
        failures = 0
        # A source re-derived for a newer contract that fails keeps its older facts and
        # is tried again next scan; it never stops the scan (docs/processing.md).
        contract_failures = 0
        aborted = False
        for candidate in candidates:
            deterministic_items = deterministic_calendar_items(candidate)
            document = (
                candidate_document(candidate, class_labels=class_labels)
                if deterministic_items is None
                else None
            )
            cache_key = (
                derivation_cache_key(candidate, model=request.model, document=document)
                if document is not None
                else deterministic_derivation_cache_key(candidate)
            )
            cached = store.cached_derivation(cache_key)
            if cached and cached["status"] == "complete":
                status = "cached"
                if purpose == "live" and cached["purpose"] != "live":
                    store.activate_derivation(int(cached["id"]))
                    status = "activated_cache"
                results.append(
                    {
                        "source": candidate["source_type"],
                        "source_key": candidate["source_key"],
                        "status": status,
                        "derivation_id": cached["id"],
                    }
                )
                continue
            if cached and cached["status"] == "running":
                results.append(
                    {
                        "source": candidate["source_type"],
                        "source_key": candidate["source_key"],
                        "status": "already_running",
                        "derivation_id": cached["id"],
                    }
                )
                continue
            if cached:
                derivation_id = int(cached["id"])
                store.restart_derivation(derivation_id)
            else:
                derivation_id = store.begin_derivation(
                    harvest_run_id=candidate["harvest_run_id"],
                    cache_key=cache_key,
                    provider=PROVIDER if document is not None else "local",
                    model=(
                        request.model
                        if document is not None
                        else ROUTINE_CALENDAR_FILTER_VERSION
                    ),
                    prompt_version=(
                        PROMPT_VERSION
                        if document is not None
                        else ROUTINE_CALENDAR_FILTER_VERSION
                    ),
                    taxonomy_version=TAXONOMY_VERSION,
                    purpose=purpose,
                )
            try:
                if deterministic_items is not None:
                    response = {
                        "items": deterministic_items,
                        "response_id": "",
                        "usage": {
                            "method": ROUTINE_CALENDAR_FILTER_VERSION,
                            "total_tokens": 0,
                            "cost": 0,
                        },
                    }
                    items = deterministic_items
                else:
                    assert client is not None and document is not None
                    response = client.derive(document)
                    items = validate_items(
                        response["items"], candidate=candidate, document=document
                    )
                item_ids = store.complete_derivation(
                    derivation_id,
                    items=items,
                    sources=candidate["sources"],
                    response_id=response["response_id"],
                    usage=response["usage"],
                )
                results.append(
                    {
                        "source": candidate["source_type"],
                        "source_key": candidate["source_key"],
                        "children": sorted(
                            {source["child"] for source in candidate["sources"]}
                        ),
                        "status": "complete",
                        "processing": (
                            "deterministic"
                            if deterministic_items is not None
                            else "llm"
                        ),
                        "derivation_id": derivation_id,
                        "item_ids": item_ids,
                        "item_count": len(item_ids),
                    }
                )
            except Exception as error:
                if candidate.get("change") == "contract":
                    contract_failures += 1
                else:
                    failures += 1
                store.fail_derivation(derivation_id, str(error))
                results.append(
                    {
                        "source": candidate["source_type"],
                        "source_key": candidate["source_key"],
                        "status": "failed",
                        "error": str(error),
                    }
                )
                if isinstance(error, OpenRouterHTTPError) and error.status_code in {
                    401,
                    402,
                    403,
                }:
                    aborted = True
                    break
        summary = {
            "run_id": request.run_id or store.latest_completed_run_id(),
            "selection": "backfill" if backfill_since else "changes",
            "backfill_since": backfill_since.isoformat() if backfill_since else None,
            "purpose": purpose,
            "provider": PROVIDER,
            "model": request.model,
            "prompt_version": PROMPT_VERSION,
            "taxonomy_version": TAXONOMY_VERSION,
            "prompt_manifest_hash": derivation_manifest().content_hash,
            "candidate_count": len(candidates),
            "llm_candidate_count": len(candidates) - deterministic_count,
            "deterministic_candidate_count": deterministic_count,
            "processed_count": len(results),
            "contract_candidate_count": migrated,
            "contract_failure_count": contract_failures,
            "failures": failures,
            "aborted": aborted,
            "results": results,
        }
        if request.migrate:
            summary["continuity"] = _continuity(before, _workflow_keys(store, as_of))
    return StageResult(2 if failures else 0, summary)


# The board's window for facts the migration re-derives: its context's reach.
MIGRATION_DAYS = 18


def _window_start(as_of: date) -> date:
    """Where the board's window starts: the Monday of the week it shows, as its
    context does (`dashboard_context.export_context`), or `as_of` when that is earlier."""
    from datetime import time

    from skoleklar.dashboard_timing import ZONE, dashboard_focus
    focus = dashboard_focus(datetime.combine(as_of, time(12), ZONE)).school_date
    return min(as_of, focus - timedelta(days=focus.weekday()))


def _plan_keys(as_of: date) -> set[str]:
    """This week's and next week's weekly-plan source keys, in either week spelling."""
    keys = set()
    for day in (as_of, as_of + timedelta(days=7)):
        year, week, _ = day.isocalendar()
        keys |= {f"class:{week}-{year}", f"class:{week:02d}-{year}"}
    return keys


def _workflow_keys(store: Any, as_of: date) -> dict[str, dict[str, Any]]:
    """The current facts from `as_of` on that the family has already seen or ticked off."""
    start, end = _window_start(as_of).isoformat(), (as_of + timedelta(days=MIGRATION_DAYS)).isoformat()
    result = {}
    for item in store.current_derived_items():
        seen = {"delivered": bool(item.get("first_delivered_at")),
                "acknowledged": bool(item.get("acknowledged_at")),
                "completed": bool(item.get("completed_at") or item.get("dismissed_at"))}
        day = item.get("effective_date") or ""
        if any(seen.values()) and start <= day <= end:
            result[item["stable_key"]] = {"child": item["child"], "type": item["item_type"],
                                          "effective_date": day, "title": item["title"], **seen}
    return result


def _continuity(before: dict[str, dict[str, Any]], after: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Which seen or ticked-off facts kept their stable key through a re-derivation.

    A re-derivation that rewords a fact's dedupe hint gives it a new key, and the
    phone's acknowledgement stays with the old one. Run `derive --migrate` on a
    copy first and read `lost` before the live scan does it.
    """
    lost = [{"stable_key": key, **value} for key, value in before.items() if key not in after]
    return {"kept_count": len(before) - len(lost), "lost_count": len(lost), "lost": lost}


def archive(request: ArchiveRequest) -> StageResult:
    database = (
        Path(request.db).expanduser().resolve()
        if request.db
        else snapshot_db_path()
    )
    if not database.exists():
        print("No snapshot database. Run `uv run skoleklar harvest` first.", file=sys.stderr)
        return StageResult(2)
    try:
        as_of = date.fromisoformat(request.as_of)
    except ValueError:
        print("--as-of must be an ISO date such as 2026-09-12.", file=sys.stderr)
        return StageResult(2)
    try:
        resolve_message_mode(request.message_mode)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return StageResult(2)

    with SnapshotStore(
        database
    ) as store:
        items = store.current_derived_items()
        plan = plan_archival(items, as_of=as_of)
        policy_decisions = plan["decisions"]
        agent_candidates = plan["agent_candidates"]
        archived_by_policy = sum(
            item["decision"] == "archive" for item in policy_decisions
        )
        summary = {
            "as_of": as_of.isoformat(),
            "policy_version": ARCHIVAL_POLICY_VERSION,
            "prompt_manifest_hash": archival_manifest().content_hash,
            "active_item_count": len({item["stable_key"] for item in items}),
            "policy_archive_count": archived_by_policy,
            "policy_retain_count": len(policy_decisions) - archived_by_policy,
            "agent_review_count": len(agent_candidates),
        }
        if request.dry_run:
            return StageResult(0, {
                        **summary,
                        "mode": "dry_run",
                        "api_call": False,
                        "persistence": False,
                        "agent_candidates": agent_candidates,
                    })

        usage: dict[str, Any] = {}
        agent_decisions: list[dict[str, Any]] = []
        if agent_candidates and not request.no_agent_review:
            api_key = openrouter_api_key()
            if not api_key:
                print(
                    "Ambiguous archival items require agent review, but no OpenRouter "
                    "API key is configured.",
                    file=sys.stderr,
                )
                return StageResult(2)
            client = OpenRouterArchivalClient(
                api_key=api_key,
                model=request.model,
                base_url=os.environ.get("OPENROUTER_BASE_URL", DEFAULT_BASE_URL),
            )
            try:
                response = client.review(
                    archival_document(agent_candidates, as_of=as_of)
                )
                agent_decisions = materialize_agent_decisions(
                    response["output"], candidates=agent_candidates
                )
                usage = response["usage"]
            except Exception as error:
                return StageResult(2, {**summary, "status": "failed", "error": str(error)})
        elif agent_candidates:
            agent_decisions = [
                {
                    "item_id": item["item_id"],
                    "stable_key": item["stable_key"],
                    "decision": "retain",
                    "reason": "agent_review_disabled",
                    "method": "policy",
                }
                for item in agent_candidates
            ]

        run_id = store.apply_archival(
            as_of=as_of,
            policy_version=ARCHIVAL_POLICY_VERSION,
            decisions=policy_decisions + agent_decisions,
            usage=usage,
        )
        report = store.archival_report(run_id)
    return StageResult(0, {"status": "complete", **(report or {})})


def consolidate(request: ConsolidateRequest) -> StageResult:
    database = (
        Path(request.db).expanduser().resolve()
        if request.db
        else snapshot_db_path()
    )
    if not database.exists():
        print("No snapshot database. Run `uv run skoleklar harvest` first.", file=sys.stderr)
        return StageResult(2)
    try:
        as_of = date.fromisoformat(request.as_of)
    except ValueError:
        print("--as-of must be an ISO date such as 2026-08-24.", file=sys.stderr)
        return StageResult(2)
    if request.limit < 1 or request.limit > 200:
        print("--limit must be between 1 and 200.", file=sys.stderr)
        return StageResult(2)
    try:
        resolve_message_mode(request.message_mode)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return StageResult(2)

    with SnapshotStore(
        database
    ) as store:
        items = [
            item
            for item in store.current_derived_items()
            if not item.get("completed_at") and not item.get("dismissed_at")
        ]
        if len(items) > request.limit:
            print(
                f"Current consolidation set has {len(items)} items, above --limit "
                f"{request.limit}. Increase the explicit limit to avoid a partial partition.",
                file=sys.stderr,
            )
            return StageResult(2)
        document = consolidation_document(items, as_of=as_of)
        cache_key = consolidation_cache_key(
            items, model=request.model, document=document
        )
        base_summary = {
            "harvest_run_id": store.latest_completed_run_id(),
            "as_of": as_of.isoformat(),
            "provider": PROVIDER,
            "model": request.model,
            "prompt_version": CONSOLIDATION_VERSION,
            "taxonomy_version": consolidation_manifest().taxonomy_version,
            "prompt_manifest_hash": consolidation_manifest().content_hash,
            "input_count": len(items),
        }
        if request.dry_run:
            return StageResult(0, {
                        **base_summary,
                        "mode": "dry_run",
                        "api_call": False,
                        "persistence": False,
                        "items": document["items"],
                    })
        if not items:
            return StageResult(0, {**base_summary, "status": "no_items"})

        cached = store.cached_consolidation(cache_key)
        if cached and cached["status"] == "complete":
            report = store.consolidation_report(int(cached["id"]))
            return StageResult(0, {"status": "cached", **(report or {})})
        if cached and cached["status"] == "running":
            return StageResult(0, {
                        **base_summary,
                        "status": "already_running",
                        "consolidation_id": cached["id"],
                    })

        api_key = openrouter_api_key()
        if not api_key:
            print(
                "No OpenRouter API key. Set OPENROUTER_API_KEY or run "
                "`uv run skoleklar llm-credentials set`.",
                file=sys.stderr,
            )
            return StageResult(2)
        if cached:
            consolidation_id = int(cached["id"])
            store.restart_consolidation(consolidation_id)
        else:
            consolidation_id = store.begin_consolidation(
                harvest_run_id=store.latest_completed_run_id(),
                cache_key=cache_key,
                provider=PROVIDER,
                model=request.model,
                prompt_version=CONSOLIDATION_VERSION,
                taxonomy_version=consolidation_manifest().taxonomy_version,
                manifest_hash=consolidation_manifest().content_hash,
                input_count=len(items),
            )
        client = OpenRouterConsolidationClient(
            api_key=api_key,
            model=request.model,
            base_url=os.environ.get("OPENROUTER_BASE_URL", DEFAULT_BASE_URL),
        )
        try:
            response = client.consolidate(document)
            partition, repairs = repair_consolidation_output(
                response["output"], items=items
            )
            consolidated = materialize_consolidation(partition, items=items)
            store.complete_consolidation(
                consolidation_id,
                items=consolidated,
                response_id=response["response_id"],
                usage=response["usage"],
            )
        except Exception as error:
            store.fail_consolidation(consolidation_id, str(error))
            return StageResult(2, {
                        **base_summary,
                        "status": "failed",
                        "consolidation_id": consolidation_id,
                        "error": str(error),
                    })
        report = store.consolidation_report(consolidation_id)
    return StageResult(0, {"status": "complete", **(report or {}), **repairs})


def manifest(request: ManifestRequest) -> StageResult:
    database = (
        Path(request.db).expanduser().resolve()
        if request.db
        else snapshot_db_path()
    )
    if not database.exists():
        print("No snapshot database. Run `uv run skoleklar harvest` first.", file=sys.stderr)
        return StageResult(2)
    try:
        target_date = date.fromisoformat(request.date)
    except ValueError:
        print("--date must be an ISO date such as 2026-08-24.", file=sys.stderr)
        return StageResult(2)
    if request.delivery_root and request.delivery_bucket:
        print("Choose either --delivery-root or --delivery-bucket.", file=sys.stderr)
        return StageResult(2)
    try:
        message_mode = resolve_message_mode(request.message_mode)
        message_processor = resolve_message_processor(
            request.message_processor
        )
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return StageResult(2)
    with SnapshotStore(
        database
    ) as snapshot:
        items = delivery_items_from_snapshot(
            snapshot, message_mode=message_mode
        )
        coverage = snapshot.message_coverage(
            mode=message_mode, processor=message_processor
        )
        manifest = build_delivery_manifest(
            items, target_date=target_date, coverage=coverage
        )
    if request.dry_run:
        return StageResult(0, manifest)
    delivery = (
        GCSDeliveryStore(request.delivery_bucket)
        if request.delivery_bucket
        else LocalDeliveryStore(
            Path(request.delivery_root).expanduser().resolve()
            if request.delivery_root
            else state_dir() / "delivery"
        )
    )
    pointer = publish_manifest(delivery, manifest)
    return StageResult(0, {
                "status": "published",
                "manifest_id": manifest["manifest_id"],
                "object": pointer["object"],
                "calendar_count": len(manifest["calendar"]),
                "reminder_count": len(manifest["reminders"]),
                "review_count": len(manifest["review"]),
                "expires_at": manifest["expires_at"],
            })
