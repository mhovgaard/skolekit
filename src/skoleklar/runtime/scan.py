"""One scan, the same wherever it runs: every stage, check and output, around a runtime.

The scan stages, checks and outputs are shared by local and GCP execution.
A runtime supplies the state and output boundaries:

- `start()`: the model, message mode and processor, timezone and run ID;
- `workspace()`: a private working directory;
- `checkout(state_dir)` and `commit(state_dir, token)`: the database and
  session, checked out and committed with runtime preconditions;
- `delivery`, `display_store()`, `status_store()`: where the phone manifest,
  the board frames and the board's own status reports live;
- `read_private(name)`, `write_private(name, text)`: the board's private
  composition files;
- `write_control(name, document)` and `lock()`: the run summary and the one
  scan at a time.

The events keep their names, fields and logger (`agskoleintra.cloud_job`), so
the cloud's logs read as before; `tests_python/test_cloud_scan_pinned.py`
pins them and the cloud's storage trail.

"""
from __future__ import annotations

import asyncio
import contextlib
import io
import json
import logging
import os
import re
import sqlite3
import time
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Protocol
from zoneinfo import ZoneInfo

from skoleklar import pipeline
from skoleklar.delivery_manifest import (
    build_delivery_manifest,
    delivery_items_from_snapshot,
    publish_manifest,
)
from skoleklar.household import load_site_profile
from skoleklar.meaning import load_meaning
from skoleklar.pipeline import (
    ArchiveRequest,
    ConsolidateRequest,
    DeriveRequest,
    HarvestRequest,
    StageResult,
)
from skoleklar.runtime.config import load_runtime_config, require_writer
from skoleklar import health, processing_cost
from skoleklar.storage import SnapshotStore
from skoleklar.triage.prompt_manifest import household_slots
from skoleklar.triage.review_checks import apply_verdicts
from skolekit.state import using_state_dir

logger = logging.getLogger("agskoleintra.cloud_job")
# The database and the session each scan checks out and commits; the profile files are read only.
DATABASE = "school-restricted.db"
SESSION = "auth-state.json"


@dataclass(frozen=True)
class ScanSettings:
    model: str
    message_mode: str
    message_processor: str
    timezone: ZoneInfo
    refresh_id: str


class Runtime(Protocol):
    """Where a scan runs: what differs between the parent's machine and Google Cloud."""
    name: str
    delivery: Any

    def start(self) -> ScanSettings: ...
    def workspace(self) -> contextlib.AbstractContextManager[str]: ...
    def checkout(self, state_dir: Path) -> Any: ...
    def commit(self, state_dir: Path, token: Any) -> None: ...
    def display_store(self) -> Any | None: ...
    def status_store(self) -> Any | None: ...
    def read_private(self, name: str) -> bytes | None: ...
    def write_private(self, name: str, text: str) -> None: ...
    def write_control(self, name: str, document: dict[str, Any]) -> None: ...
    def lock(self) -> contextlib.AbstractContextManager[None]: ...


def _event(name: str, **fields: Any) -> None:
    logger.info(json.dumps({"event": name, **fields}, separators=(",", ":")))


def _check_invariants(database: Path) -> None:
    """Stop before the upload if the formal store breaks a rule (data foundation §9)."""
    from skoleklar import invariants
    started = time.monotonic()
    with contextlib.closing(sqlite3.connect(database)) as connection:
        failures = invariants.check(connection, integrity=False)
    duration_seconds = round(time.monotonic() - started, 3)
    if failures:
        # Names only: the offending rows can hold school data.
        names = [name for name, _ in failures]
        _event("invariants_failed", checks=names, duration_seconds=duration_seconds)
        raise RuntimeError("Invariant checks failed: " + "; ".join(names))
    _event("invariants_passed", check_count=len(invariants.CHECKS),
           duration_seconds=duration_seconds)


def _prepare_sqlite(database: Path) -> None:
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        result = connection.execute("PRAGMA integrity_check").fetchone()
    if result is None or result[0] != "ok":
        raise RuntimeError("SQLite integrity check failed")


def _stage_summary(stage: str, payload: dict[str, Any] | None) -> dict[str, Any]:
    """The safe counts from a stage's report; never its items or text."""
    if not isinstance(payload, dict):
        return {}
    if stage == "derive":
        summary = {
            key: int(payload[key])
            for key in ("candidate_count", "contract_candidate_count", "contract_failure_count", "failures")
            if isinstance(payload.get(key), int)
        }
        if isinstance(payload.get("contract_backoff_count"), int) and payload["contract_backoff_count"]:
            summary["contract_backoff_count"] = payload["contract_backoff_count"]
        # Why a re-derivation for a newer contract failed, as a fixed code; never its text.
        failed = [result for result in payload.get("results") or []
                  if isinstance(result, dict) and result.get("contract") and result.get("status") == "failed"]
        if failed:
            summary["contract_failure_code"] = _safe_failure_code({"results": failed})
        # How many seen or ticked-off facts a re-derivation gave a new key; never which.
        continuity = payload.get("continuity")
        if isinstance(continuity, dict) and isinstance(continuity.get("lost_count"), int):
            summary["lost_key_count"] = continuity["lost_count"]
        return summary
    if stage == "consolidate":
        return {
            key: int(payload[key])
            for key in (
                "input_count",
                "output_count",
                "reduction_count",
                "dropped_group_count",
                "unknown_id_count",
                "repeated_id_count",
                "omitted_id_count",
            )
            if isinstance(payload.get(key), int)
        }
    return {}


def _safe_failure_code(payload: dict[str, Any] | None) -> str:
    if not isinstance(payload, dict):
        return "command_failed"
    errors: list[str] = []
    if isinstance(payload, dict):
        if isinstance(payload.get("error"), str):
            errors.append(payload["error"])
        for result in payload.get("results") or []:
            if isinstance(result, dict) and isinstance(result.get("error"), str):
                errors.append(result["error"])
    combined = " ".join(errors)
    match = re.search(r"API returned (\d{3})", combined)
    if match:
        return f"provider_http_{match.group(1)}"
    lowered = combined.casefold()
    if "timed out" in lowered or "timeout" in lowered:
        return "provider_timeout"
    if "no endpoints" in lowered:
        return "provider_routing"
    if "json" in lowered or "expecting value" in lowered:
        return "invalid_provider_json"
    if "consolidation" in lowered or "item ids" in lowered:
        return "consolidation_validation"
    return "command_failed"


def _dashboard_failure_code(error: Exception) -> str:
    """Classify composition failures without logging source or model content."""
    text = str(error).casefold()
    known = {
        "dashboard dates must follow the selected school week": "wrong_school_week",
        "private event row must include its retained bring-item": "missing_private_event_item",
        "a board line names an unknown fact": "unknown_reference",
        "a board line must look as its facts say": "presentation_mismatch",
        "a displayed fact must be on the line its audit names": "audit_location_mismatch",
        "audit must account for each context fact exactly once": "incomplete_audit",
        "displayed fact date must match dashboard location": "misplaced_fact",
        "confirmed weekend activity must be displayed": "missing_weekend_activity",
        "cancelled activity must not appear as a plan": "cancelled_activity_shown",
        "distinct weekend activities must not share a row": "merged_distinct_activities",
        "weekend overflow": "weekend_overflow_miscounted",
        "birthday notice is not a family activity": "birthday_notice_shown",
    }
    for marker, code in known.items():
        if marker in text:
            return code
    if "dashboard composition failed validation" in text:
        return "composition_validation"
    return "dashboard_error"


def _run_quietly(stage: str, callback: Callable[[], StageResult]) -> dict[str, Any]:
    """Run one service; only its safe counts reach the log, never its output or messages.

    Returns those safe counts.
    """
    started = time.monotonic()
    _event("stage_started", stage=stage)
    with (contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()),
          processing_cost.stage(stage)):
        result = callback()
    duration_seconds = round(time.monotonic() - started, 3)
    summary = _stage_summary(stage, result.output)
    if not result.ok:
        _event(
            "stage_failed",
            stage=stage,
            exit_code=result.exit_code,
            failure_code=_safe_failure_code(result.output),
            duration_seconds=duration_seconds,
            **summary,
        )
        raise RuntimeError(f"{stage} failed with exit code {result.exit_code}")
    _event(
        "stage_completed",
        stage=stage,
        duration_seconds=duration_seconds,
        **summary,
    )
    return summary


def _utc_timestamp(value: str) -> str:
    return (
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        .astimezone(UTC)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _ingest_delivery_state(snapshot: SnapshotStore, delivery: Any) -> dict[str, int]:
    completed = 0
    delivered = 0
    for _, observation in delivery.list_json("completion-observations"):
        completed += snapshot.complete_delivery_keys(
            observation.get("completed_stable_keys", []),
            at=_utc_timestamp(observation["observed_at"]),
        )
    for _, receipt in delivery.list_json("receipts"):
        delivered += snapshot.record_delivery_keys(
            receipt.get("applied_stable_keys", []),
            at=_utc_timestamp(receipt["finished_at"]),
        )
    return {"completed_item_count": completed, "delivered_item_count": delivered}


def _check_reviews(database: Path, *, model: str, message_mode: str, today) -> dict[str, str]:
    """Settle the facts flagged for review in the board's period; a failure never stops the sync.

    Returns the stage's health entry, which the board and the brief show.
    """
    from skoleklar.settings import openrouter_api_key
    from skoleklar.triage.derivation import OpenRouterClient
    from skoleklar.triage.review_checks import check_reviews
    started = time.monotonic()
    _event("stage_started", stage="review_checks")
    try:
        client = OpenRouterClient(api_key=openrouter_api_key(), model=model)
        with SnapshotStore(database) as snapshot:
            counts = check_reviews(snapshot, today=today, client=client, message_mode=message_mode)
    except Exception as error:
        _event("review_checks_failed", error_type=type(error).__name__,
               duration_seconds=round(time.monotonic() - started, 3))
        return health.degraded(error)
    _event("stage_completed", stage="review_checks",
           duration_seconds=round(time.monotonic() - started, 3), **counts)
    # A model call that failed leaves flagged facts unchecked: the board says so.
    return health.degraded("model_failed") if counts.get("model_failed") else health.ok()


def _generate_day_summaries(database: Path, *, model: str, message_mode: str, today) -> dict[str, str]:
    """Retell each new weekly plan per day; a failure never stops the sync.

    Returns the stage's health entry: degraded when it failed or a plan failed.
    """
    from skoleklar.settings import openrouter_api_key
    from skoleklar.day_summaries import generate_day_summaries
    from skoleklar.triage.derivation import OpenRouterClient
    started = time.monotonic()
    _event("stage_started", stage="day_summaries")
    try:
        client = OpenRouterClient(api_key=openrouter_api_key(), model=model)
        with SnapshotStore(database) as snapshot:
            result = generate_day_summaries(snapshot, client, model=model, today=today)
    except Exception as error:
        _event("day_summaries_failed", error_type=type(error).__name__,
               duration_seconds=round(time.monotonic() - started, 3))
        return health.degraded(error)
    _event("stage_completed", stage="day_summaries",
           duration_seconds=round(time.monotonic() - started, 3),
           **{key: result[key] for key in ("candidate_count", "reused_count", "generated_count",
                                           "failed_count", "skipped_count")},
           **({"failures": result["failures"]} if result["failures"] else {}))
    return health.degraded("plan_failed") if result["failed_count"] else health.ok()


def _day_summary_extras(snapshot: SnapshotStore):
    """Attach the stored day summaries to a frame; the board never depends on them."""
    from skoleklar.dashboard_renderer import validate
    from skoleklar.day_summaries import attach_day_summaries, summary_lookup
    lookup = summary_lookup(snapshot)
    reported = []

    def extras(state):
        try:
            result = attach_day_summaries(state, lookup)
            validate(result)
            return result
        except Exception as error:
            if not reported:
                reported.append(True)
                _event("day_summaries_unattached", error_type=type(error).__name__)
            return health.record(state, "day_summaries", health.degraded(error))
    return extras


def _day_view_extras(snapshot: SnapshotStore):
    """Attach the children's day view in its morning and afternoon windows.

    `AG_DAY_VIEW=off` keeps the parents' board all day. Like the summaries, the
    board never depends on it: a failure leaves the frame as it was.
    """
    from skoleklar.dashboard_renderer import validate
    from skoleklar.day_view import attach_day_view, lesson_lookup
    if os.environ.get("AG_DAY_VIEW", "").strip().lower() == "off":
        return lambda state: state
    lookup = lesson_lookup(snapshot)
    reported = []

    def extras(state):
        try:
            result = attach_day_view(state, lookup)
            validate(result)
        except Exception as error:
            if not reported:
                reported.append(True)
                _event("day_view_unattached", error_type=type(error).__name__)
            return health.record(state, "day_view", health.degraded(error))
        if "day_view" in result:
            _event("day_view_attached", date=result["day_view"]["date"],
                   lesson_count=sum(len(child["lessons"]) for child in result["day_view"]["children"]))
        return result
    return extras


def _frame_extras(snapshot: SnapshotStore):
    """The day summaries, then the children's view, which reads them."""
    summaries, day_view = _day_summary_extras(snapshot), _day_view_extras(snapshot)
    return lambda state: day_view(summaries(state))


def _with_weekend_weather(state: dict[str, Any], now: datetime, *, fetch=None) -> dict[str, Any]:
    """Add the weekend or school-day forecast; the dashboard never depends on it."""
    from skoleklar.weather import attach_weather
    try:
        result = attach_weather(state, now, **({"fetch": fetch} if fetch else {}))
    except Exception as error:
        _event("weather_failed", error_type=type(error).__name__)
        return health.record(state, "weather", health.degraded(error))
    if "weather" in result:
        _event("weather_attached", verdict=result["weather"]["verdict"],
               day_count=len(result["weather"]["days"]))
    elif "day_weather" in result:
        _event("day_weather_attached", day_count=len(result["day_weather"]))
    return result


def _forecast_once():
    """A forecast fetch that asks Open-Meteo once per day range during one run."""
    from skoleklar import weather
    results = {}

    def fetch(config, days):
        key = (days[0], days[-1])
        if key not in results:
            results[key] = weather.fetch_forecast(config, days)
        return results[key]
    return fetch


def _load_composition_cache(runtime: Runtime, scan_health: dict[str, Any] | None = None) -> dict[str, Any]:
    """Last run's compositions by key; a missing or unreadable cache only costs a model call.

    An unreadable cache is recorded in `scan_health`; the board names no missing part for it.
    """
    try:
        data = runtime.read_private("composition-cache.json")
        return {} if data is None else json.loads(data)["weeks"]
    except Exception as error:
        _event("composition_cache_unavailable", error_type=type(error).__name__)
        if scan_health is not None:
            scan_health["composition_cache"] = health.degraded(error)
        return {}


def _with_battery(frames, now, store=None):
    """Each frame with the board's battery from its latest status report, when recent.

    The parents' board shows it in the top right. A missing, old or unreadable
    report leaves the frames as they are. `store` is the runtime's status store;
    by default the environment's.
    """
    try:
        from skoleklar.board_status import latest_record, status_store_from_environment
        record = latest_record(store if store is not None else status_store_from_environment())
        if not record:
            return frames
        report = record["report"]
        received = datetime.fromisoformat(str(record["received_at"]).replace("Z", "+00:00"))
        level = report.get("battery_percent")
        if (report.get("battery_present") is not True or now - received > timedelta(hours=48)
                or not isinstance(level, int | float) or not 0 <= level <= 100):
            return frames
    except Exception as error:
        _event("device_battery_unavailable", error_type=type(error).__name__)
        return [(at, health.record(state, "battery", health.degraded(error))) for at, state in frames]
    device = {"battery_percent": round(level)}
    return [(at, {**state, "device": device}) for at, state in frames]


def _with_lookahead(frame, lookahead, at):
    """The parents' board's days ahead and later events; the board never depends on them."""
    from skoleklar.parents_board import attach_lookahead
    try:
        context = lookahead(at)
        frame = attach_lookahead(frame, context)
        # The days ahead read their own, longer context: its facts must be current too.
        # The weekend cover and the children's view draw no days ahead, so they skip it.
        shows_days_ahead = not (frame.get("weather") or frame.get("day_view"))
        if shows_days_ahead and (context.get("contract") or {}).get("stale"):
            frame = health.record(frame, "facts", health.degraded("old_contract"))
        return frame
    except Exception as error:
        _event("lookahead_failed", error_type=type(error).__name__)
        return health.record(frame, "lookahead", health.degraded(error))


def _dashboard_frames(now, context_at, compose_week, cached, model, stage, extras=None, lookahead=None,
                      scan_health=None):
    """Compose each school week once and derive the state for every layout change.

    Returns the (start, state) frames, the composition key, state and audit per
    school week, and the context at `now`. A week whose inputs match `cached`
    reuses that composition. `extras` adds data the model never composes, such as
    the day summaries, to each frame's state. `lookahead(at)` is a context reaching
    two weeks out, for the parents' board's days ahead and later events.
    `scan_health` holds the earlier stages' health entries; every frame carries them.
    """
    from skoleklar.dashboard_composition import composition_key, reuse_composition
    from skoleklar.dashboard_timeline import TIMELINE_HOURS, school_week, slice_starts, slice_state
    weeks = {}

    def week_state(context, at):
        week = school_week(at)
        if week not in weeks:
            stage["name"] = "compose"
            key = composition_key(context, model)
            rejected = []
            reused = reuse_composition(context, cached[key], rejected) if key in cached else None
            # Whether the last scan's composition was reused, and why not: a miss means
            # the inputs changed; a rejection names the fixed validator reason.
            _event("composition_cache", week=week.isoformat(),
                   result="reused" if reused else "rejected" if rejected else "miss",
                   **({"reason": rejected[0]} if rejected else {}))
            fallback = False
            if reused:
                state, audit = reused
            else:
                try:
                    state, audit = compose_week(context)
                except Exception as error:
                    state, audit = _fallback_week(context, week, error)
                    fallback = True
            # Health belongs to this scan, never to a cached week (skoleklar.health).
            state = {**state, "health": {"composition": health.degraded("plain_board") if fallback
                                         else health.ok()}}
            weeks[week] = {"key": key, "state": state, "audit": audit,
                           "reused": reused is not None, "fallback": fallback}
        return weeks[week]["state"]

    stage["name"] = "context"
    first = context_at(now)
    week_state(first, now)
    stage["name"] = "context"
    expires = datetime.fromisoformat(first["freshness"]["expires_at"])
    fetch = _forecast_once()
    frames = []
    for at in slice_starts(now, min(now + timedelta(hours=TIMELINE_HOURS), expires)):
        context = first if at == now else context_at(at)
        state = week_state(context, at)
        stage["name"] = "weather"
        frame = health.merge(slice_state(state, context), scan_health or {})
        # Facts on the board made under an older contract than it reads, at this moment.
        stale = (context.get("contract") or {}).get("stale")
        frame = health.record(frame, "facts", health.degraded("old_contract") if stale else health.ok())
        frame = _with_weekend_weather(frame, at, fetch=fetch)
        if extras is not None:
            stage["name"] = "extras"
            frame = extras(frame)
        if lookahead is not None:
            stage["name"] = "lookahead"
            frame = _with_lookahead(frame, lookahead, at)
        frames.append((at, frame))
        stage["name"] = "context"
    return frames, weeks, first


def _fallback_week(context, week, error):
    """A plain board from the facts when composition fails, so the panel stays current.

    The composition's own failure is logged; if the fallback fails too, that
    original error is raised and the dashboard keeps its previous timeline.
    """
    from skoleklar.dashboard_build import fallback_composition
    attempts = getattr(error, "attempts", None)
    try:
        result = fallback_composition(context)
    except Exception as fallback_error:
        _event("composition_fallback_failed", week=week.isoformat(),
               error_type=type(fallback_error).__name__)
        raise error
    _event("composition_fallback", week=week.isoformat(), error_type=type(error).__name__,
           failure_code=_dashboard_failure_code(error), **({"attempts": attempts} if attempts else {}))
    return result


def run_scan(runtime: Runtime) -> dict[str, Any]:
    """One scan with every stage, on `runtime`; returns the run summary."""
    settings = runtime.start()
    processing_cost.reset()
    model, message_mode = settings.model, settings.message_mode
    message_processor, timezone, refresh_id = settings.message_processor, settings.timezone, settings.refresh_id
    target_date = datetime.now(timezone).date()

    # The scan works in its own directory. The process environment is left alone;
    # the directory reverts when the scan ends (settings.using_state_dir).
    with runtime.workspace() as temporary, using_state_dir(Path(temporary)) as state_dir:
        state_dir.chmod(0o700)
        token = runtime.checkout(state_dir)
        # The household's school.toml, profile.toml and skoleklar.toml, when the
        # runtime has them (skolekit.profile, meaning.py). Read now, so a broken
        # file stops the scan before any work; they are never written back.
        load_site_profile(state_dir)
        load_meaning(state_dir)
        household_slots(state_dir)
        # One writer: a scan stops here if the household's config names the other place.
        require_writer(load_runtime_config(state_dir), runtime.name)
        database = state_dir / DATABASE
        delivery_store = runtime.delivery

        if database.exists():
            with SnapshotStore(
                database
            ) as snapshot:
                ingestion = _ingest_delivery_state(snapshot, delivery_store)
            _event("delivery_state_ingested", **ingestion)

        harvest = HarvestRequest(
            db=str(database),
            mode="daily",
            auto_login=True,
            auto_login_timeout=2,
            message_mode=message_mode,
            message_processor=message_processor,
            model=model,
        )
        _run_quietly("harvest", lambda: asyncio.run(pipeline.harvest(harvest)))
        # A fact the board reads must carry the current contract (skoleklar.fact):
        # the scan re-derives those in the board's window that do not.
        derive = DeriveRequest(db=str(database), model=model, message_mode=message_mode,
                               migrate=True, as_of=target_date.isoformat())
        derived = _run_quietly("derive", lambda: pipeline.derive(derive)) or {}
        archive_plan = ArchiveRequest(as_of=target_date.isoformat(), db=str(database), model=model,
                                      dry_run=True, message_mode=message_mode)
        _run_quietly("archive_plan", lambda: pipeline.archive(archive_plan))
        archive = replace(archive_plan, dry_run=False)
        _run_quietly("archive", lambda: pipeline.archive(archive))
        consolidate = ConsolidateRequest(as_of=target_date.isoformat(), db=str(database),
                                         limit=200, model=model, message_mode=message_mode)
        _run_quietly("consolidate", lambda: pipeline.consolidate(consolidate))
        # What the board and the brief say is missing (skoleklar.health).
        scan_health = {
            # A source the migration could not re-derive keeps its older facts.
            **({"facts": health.degraded("rederive_failed")}
               if derived.get("contract_failure_count") or derived.get("contract_backoff_count") else {}),
        }
        with processing_cost.stage("day_summaries"):
            scan_health["day_summaries"] = _generate_day_summaries(database, model=model,
                                                                   message_mode=message_mode, today=target_date)
        with processing_cost.stage("review_checks"):
            scan_health["review_checks"] = _check_reviews(database, model=model, message_mode=message_mode,
                                                          today=target_date)

        _check_invariants(database)
        _prepare_sqlite(database)
        runtime.commit(state_dir, token)
        # Persist the validated lifecycle before independent output work.
        outcomes = {}
        with SnapshotStore(database, read_only=True) as snapshot:
            snapshot.connection.execute("BEGIN")
            try:
                # The review checks' verdicts settle flagged facts for the phone as
                # for the board: a fact the board shows becomes an entry or reminder.
                items = apply_verdicts(snapshot, delivery_items_from_snapshot(
                    snapshot, message_mode=message_mode))
                coverage = snapshot.message_coverage(mode=message_mode, processor=message_processor)
                manifest = build_delivery_manifest(items, target_date=target_date, coverage=coverage,
                                                   health=scan_health)
                publish_manifest(delivery_store, manifest)
                outcomes["iphone"] = {"status": "published", "artifact_id": manifest["manifest_id"]}
            except Exception as error:
                outcomes["iphone"] = {"status": "failed", "error_type": type(error).__name__}
            # The board's frames are drawn wherever a display store is set: a bucket or a folder.
            displays = runtime.display_store()
            if displays is not None:
                stage = {"name": "context"}
                # The composition and the live topics: what the rest of the scan spends.
                processing_cost.set_stage("dashboard")
                try:
                    from skoleklar.dashboard_composition import compose
                    from skoleklar.dashboard_context import export_context
                    from skoleklar.dashboard_timeline import school_week
                    from skoleklar.display_store import make_timeline
                    from skoleklar.live_subjects import load_live_messages
                    from skoleklar.parents_board import CONTEXT_DAYS
                    from skoleklar.settings import openrouter_api_key
                    from skoleklar.triage.derivation import OpenRouterClient
                    confirmations = _load_event_confirmations(runtime)
                    client = OpenRouterClient(api_key=openrouter_api_key(), model=model)
                    now = datetime.now(timezone)
                    frames, weeks, context = _dashboard_frames(
                        now,
                        lambda at: export_context(snapshot, now=at, message_mode=message_mode,
                                                  confirmations=confirmations),
                        lambda context: compose(context, client),
                        _load_composition_cache(runtime, scan_health), model, stage,
                        extras=_frame_extras(snapshot), scan_health=scan_health,
                        lookahead=lambda at: export_context(snapshot, now=at, days=CONTEXT_DAYS,
                                                            message_mode=message_mode,
                                                            confirmations=confirmations))
                    fallback_weeks = sum(week["fallback"] for week in weeks.values())
                    _event("dashboard_timeline", frame_count=len(frames), week_count=len(weeks),
                           reused_week_count=sum(week["reused"] for week in weeks.values()),
                           fallback_week_count=fallback_weeks)
                    # Persist only subject-free composition for review/reproducibility.
                    stage["name"] = "persist_state"
                    current = weeks[school_week(now)]
                    runtime.write_private("current-state.json", json.dumps(frames[0][1]))
                    runtime.write_private("current-audit.json", json.dumps(current["audit"]))
                    # A fallback week is not cached, so the next scan asks the model again.
                    runtime.write_private("composition-cache.json", json.dumps(
                        {"weeks": {week["key"]: {"state": {key: value for key, value in week["state"].items()
                                                           if key != "health"},
                                                 "audit": week["audit"]}
                                   for week in weeks.values() if not week["fallback"]}}))
                    stage["name"] = "live_subjects"
                    live = None
                    if message_mode == "events-only":
                        from skoleklar.triage.message_topics import topic_writer, topics_mode
                        topics = topic_writer(client) if topics_mode() == "model" else None
                        live = load_live_messages(context["children"], topics=topics)
                    stage["name"] = "device"
                    frames = _with_battery(frames, now, runtime.status_store())
                    stage["name"] = "make_bundle"
                    bundle = make_timeline(frames, refresh_id, live_messages=live)
                    stage["name"] = "publish_bundle"
                    displays.write_json("current.json", bundle)
                    outcomes["dashboard"] = {"status": "published", "artifact_id": refresh_id,
                                             **({"fallback_weeks": fallback_weeks} if fallback_weeks else {})}
                except Exception as error:
                    dashboard_stage = stage["name"]
                    outcomes["dashboard"] = {
                        "status": "failed",
                        "error_type": type(error).__name__,
                        "failure_stage": dashboard_stage,
                    }
                    # One fixed validator reason per attempt, so a failure can be
                    # diagnosed from the log without the private state.
                    attempts = getattr(error, "attempts", None)
                    _event("dashboard_failed", failure_stage=dashboard_stage,
                           error_type=type(error).__name__,
                           failure_code=_dashboard_failure_code(error),
                           **({"attempts": attempts} if attempts else {}))
            else:
                outcomes["dashboard"] = {"status": "disabled"}
    failed = [v for v in outcomes.values() if v["status"] == "failed"]
    summary = {"status": "partial" if failed else "complete", "refresh_id": refresh_id,
               "finished_at": datetime.now(UTC).isoformat(), "outputs": outcomes}
    # What the scan missed without failing (skoleklar.health), when anything.
    degraded = {stage: entry for stage, entry in scan_health.items() if entry["status"] == "degraded"}
    if degraded:
        summary["health"] = degraded
    summary["processing"] = processing_cost.snapshot()
    runtime.write_control("latest-run.json", summary)
    _event("sync_completed", **summary)
    return summary


def run(runtime: Runtime) -> dict[str, Any]:
    """One scan under the runtime's lock; a failure still leaves a run summary."""
    with runtime.lock():
        try:
            summary = run_scan(runtime)
        except Exception as error:
            summary = {"status": "failed", "error_type": type(error).__name__,
                       "finished_at": datetime.now(UTC).isoformat(), "outputs": {},
                       "processing": processing_cost.snapshot()}
            runtime.write_control("latest-run.json", summary)
            _record_processing_cost(runtime, summary)
            raise
        _record_processing_cost(runtime, summary)
        return summary


def _record_processing_cost(runtime: Runtime, summary: dict[str, Any]) -> None:
    """Add the run to the cost history Tavlestatus draws; a failure here never fails the scan."""
    try:
        processing_cost.append_run(runtime.status_store(), finished_at=summary["finished_at"],
                                   status=summary["status"], refresh_id=summary.get("refresh_id"),
                                   totals=summary.get("processing") or processing_cost.snapshot())
    except Exception as error:
        _event("processing_cost_not_recorded", error_type=type(error).__name__)


def _load_event_confirmations(runtime: Runtime):
    data = runtime.read_private("event-confirmations.json")
    return None if data is None else json.loads(data)


