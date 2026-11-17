"""Phase 0 safety net: the cloud scan's stages, events and storage writes.

`skoleklar.cloud_job` runs three times a day. These tests run the real job
against a real (empty, migrated) SQLite store, with only the edges replaced:
an in-memory GCS, a frozen clock, a monotonic clock that ticks 0.25 s per
reading, stand-ins for the CLI commands (harvest, derive, archive,
consolidate), and a model client that fails the test if it is ever called. The
board's composition and its extras (weather, day summaries, day view,
lookahead) are replaced at their module boundary; their own behaviour is pinned
elsewhere.

What is pinned:

- the stage order: harvest, derive, archive_plan, archive, consolidate, day
  summaries, review checks, the invariant checks, the state upload, the phone
  manifest, then the board frames and the run summary;
- every `_event` name and its fields, byte for byte for a complete run, and by
  name and field set for every failure branch (`EVENT_FIELDS`);
- the GCS objects read and written, with their generation preconditions.

Phase 3 moves this orchestration into services. The events must stay
byte-identical; the commands' argument values must carry over, however they are
passed.
"""
from __future__ import annotations

import json
from dataclasses import asdict
import logging
import re
import socket
from datetime import datetime
from pathlib import Path

import pytest
from google.api_core.exceptions import NotFound, PreconditionFailed
from google.cloud import storage

from skoleklar import settings as credentials
from skoleklar import cloud_job, delivery_manifest, pipeline
from skoleklar.runtime import scan as scan_service
from skoleklar.pipeline import StageResult
from skoleklar.storage import SnapshotStore
from skolekit.profile import profile_toml
from skolekit.school import school_toml
from tests_python.skoleintra_site import ZONE, frozen_datetime
from tests_python.synthetic_household import CHILDREN, SCHOOL


NOW = datetime(2026, 11, 12, 7, 0, tzinfo=ZONE)
EXPIRES = datetime(2026, 11, 13, 18, 0, tzinfo=ZONE)
# The slice starts the stand-in timeline returns: two frames in the week of
# 9 November, then Friday 15:00, which shows the week of 16 November.
SLICES = [NOW, NOW.replace(hour=15), NOW.replace(day=13, hour=15)]
REFRESH_ID = "synthetic-execution-0001"
# The scan's events are logged from the shared scan module (phase 10); the job is its entry point.
SOURCES = (Path(cloud_job.__file__), Path(scan_service.__file__))

# Every event the job can log: (fields always present, fields that may be added).
EVENT_FIELDS: dict[str, tuple[tuple[str, ...], frozenset[str]]] = {
    "delivery_state_ingested": (("completed_item_count", "delivered_item_count"), frozenset()),
    "stage_started": (("stage",), frozenset()),
    "stage_completed": (
        ("stage", "duration_seconds"),
        frozenset({
            # derive
            "candidate_count", "failures", "contract_candidate_count", "contract_failure_count",
            "contract_backoff_count", "contract_failure_code", "lost_key_count",
            # consolidate
            "input_count", "output_count", "reduction_count", "dropped_group_count",
            "unknown_id_count", "repeated_id_count", "omitted_id_count",
            # day_summaries (candidate_count and failures as above)
            "reused_count", "generated_count", "failed_count", "skipped_count",
            # review_checks
            "flagged", "reused", "rule", "model", "unclear", "unchecked", "model_failed",
        }),
    ),
    "stage_failed": (
        ("stage", "exit_code", "failure_code", "duration_seconds"),
        frozenset({
            "candidate_count", "failures", "input_count", "output_count", "reduction_count",
            "dropped_group_count", "unknown_id_count", "repeated_id_count", "omitted_id_count",
        }),
    ),
    "day_summaries_failed": (("error_type", "duration_seconds"), frozenset()),
    "review_checks_failed": (("error_type", "duration_seconds"), frozenset()),
    "invariants_passed": (("check_count", "duration_seconds"), frozenset()),
    "invariants_failed": (("checks", "duration_seconds"), frozenset()),
    "composition_cache_unavailable": (("error_type",), frozenset()),
    "composition_cache": (("week", "result"), frozenset({"reason"})),
    "composition_fallback": (("week", "error_type", "failure_code"), frozenset({"attempts"})),
    "composition_fallback_failed": (("week", "error_type"), frozenset()),
    "weather_attached": (("verdict", "day_count"), frozenset()),
    "day_weather_attached": (("day_count",), frozenset()),
    "weather_failed": (("error_type",), frozenset()),
    "day_summaries_unattached": (("error_type",), frozenset()),
    "day_view_attached": (("date", "lesson_count"), frozenset()),
    "day_view_unattached": (("error_type",), frozenset()),
    "lookahead_failed": (("error_type",), frozenset()),
    "dashboard_timeline": (
        ("frame_count", "week_count", "reused_week_count", "fallback_week_count"), frozenset()
    ),
    "device_battery_unavailable": (("error_type",), frozenset()),
    "dashboard_failed": (("failure_stage", "error_type", "failure_code"), frozenset({"attempts"})),
    "sync_completed": (("status", "refresh_id", "finished_at", "outputs"), frozenset({"health"})),
    "sync_failed": (("error_type",), frozenset()),
}


# Fakes

UNSET = object()


class FakeGCS:
    """Buckets in memory, with GCS generation preconditions and an operation log."""

    def __init__(self):
        self.objects: dict[tuple[str, str], tuple[bytes, int]] = {}
        self.trail: list[str] = []
        self._generation = 100

    # storage.Client() returns this object.
    def __call__(self, *args, **kwargs):
        return self

    def bucket(self, name):
        return FakeBucket(self, name)

    def put(self, bucket, name, data):
        self._generation += 1
        self.objects[(bucket, name)] = (data if isinstance(data, bytes) else data.encode(), self._generation)

    def json(self, bucket, name):
        return json.loads(self.objects[(bucket, name)][0])

    def log(self, bucket, operation, name, precondition=UNSET):
        # A precondition left out is not the same call as an explicit `None`.
        suffix = "" if precondition is UNSET else f" if_generation_match={precondition}"
        self.trail.append(f"{bucket} {operation} {name}{suffix}")

    def write(self, bucket, name, data, precondition):
        current = self.objects.get((bucket, name))
        if precondition not in (None, UNSET) and (current[1] if current else 0) != precondition:
            raise PreconditionFailed(f"{bucket}/{name}")
        self.put(bucket, name, data)


class FakeBucket:
    versioning_enabled = False
    retention_period = None
    default_event_based_hold = False
    soft_delete_policy = None

    def __init__(self, gcs, name):
        self.gcs, self.name = gcs, name

    def blob(self, name):
        return FakeBlob(self.gcs, self.name, name)

    def list_blobs(self, prefix):
        self.gcs.log(self.name, "list", prefix)
        return [self.blob(name) for bucket, name in sorted(self.gcs.objects)
                if bucket == self.name and name.startswith(prefix)]

    def reload(self):
        self.gcs.log(self.name, "reload-bucket", "")


class FakeBlob:
    temporary_hold = False
    event_based_hold = False

    def __init__(self, gcs, bucket, name):
        self.gcs, self.bucket, self.name = gcs, bucket, name
        self.cache_control = None

    @property
    def generation(self):
        current = self.gcs.objects.get((self.bucket, self.name))
        return current[1] if current else None

    def _data(self):
        current = self.gcs.objects.get((self.bucket, self.name))
        if current is None:
            raise NotFound(f"{self.bucket}/{self.name}")
        return current[0]

    def reload(self):
        self.gcs.log(self.bucket, "reload", self.name)
        self._data()

    def download_to_filename(self, path):
        self.gcs.log(self.bucket, "download", self.name)
        Path(path).write_bytes(self._data())

    def download_as_bytes(self, **kwargs):
        self.gcs.log(self.bucket, "read", self.name)
        return self._data()

    def upload_from_filename(self, path, if_generation_match=UNSET):
        self.gcs.log(self.bucket, "upload", self.name, if_generation_match)
        self.gcs.write(self.bucket, self.name, Path(path).read_bytes(), if_generation_match)

    def upload_from_string(self, data, content_type=None, if_generation_match=UNSET):
        self.gcs.log(self.bucket, "write", self.name, if_generation_match)
        self.gcs.write(self.bucket, self.name, data, if_generation_match)

    def delete(self, if_generation_match=None):
        self.gcs.log(self.bucket, "delete", self.name, if_generation_match)
        if self.generation != if_generation_match:
            raise PreconditionFailed(self.name)
        del self.gcs.objects[(self.bucket, self.name)]


class Ticker:
    """`time.monotonic()` that advances 0.25 s per reading: every duration is 0.25."""

    def __init__(self):
        self.value = 1000.0

    def monotonic(self):
        self.value += 0.25
        return self.value


class ModelClient:
    """The OpenRouter client: constructions are recorded, any call fails the test."""

    created: list[dict] = []

    def __init__(self, *, api_key, model):
        ModelClient.created.append({"api_key": api_key, "model": model})

    def generate(self, *args, **kwargs):
        raise AssertionError("the model must not be called in these tests")


# The harness


class Scan:
    """One cloud scan with every edge replaced; tests adjust it before `run()`."""

    def __init__(self, monkeypatch, tmp_path):
        from skoleklar import (
            board_status, dashboard_composition, dashboard_context, dashboard_renderer,
            dashboard_timeline, day_summaries, day_view, display_store,
            live_subjects, parents_board, weather,
        )
        from skoleklar.triage import derivation

        self.monkeypatch = monkeypatch
        self.gcs = FakeGCS()
        self.commands: list[tuple[str, dict]] = []
        self.calls: list[tuple] = []
        self.exit_codes = {"harvest": 0, "derive": 0, "archive": 0, "consolidate": 0}
        self.derive_output = {
            "candidate_count": 3,
            "failures": 0,
            "results": [{"source_key": "private", "error": "private detail"}],
        }
        self.reject_cached = False
        ModelClient.created = []

        for name, value in {
            "AG_PRIVATE_BUCKET": "private",
            "AG_DELIVERY_BUCKET": "delivery",
            "AG_DISPLAY_BUCKET": "display",
            "AG_CONTROL_BUCKET": "control",
            "AG_SCHOOL_MESSAGE_MODE": "events-only",
            "AG_SCHOOL_MESSAGE_PROCESSOR": "zdr",
            "AG_LLM_MODEL": "synthetic/model-1",
            "CLOUD_RUN_EXECUTION": REFRESH_ID,
            # The job points this at its own temporary directory; monkeypatch restores it.
            "AG_SCHOOL_HOME": str(tmp_path / "home"),
        }.items():
            monkeypatch.setenv(name, value)
        for name in ("AG_TIMEZONE", "AG_DAY_VIEW", "AG_MESSAGE_TOPICS", "AG_DISPLAY_ROOT",
                     "AG_CONTROL_ROOT"):
            monkeypatch.delenv(name, raising=False)

        monkeypatch.setattr(storage, "Client", self.gcs)
        monkeypatch.setattr(scan_service, "datetime", frozen_datetime(NOW))
        monkeypatch.setattr(delivery_manifest, "datetime", frozen_datetime(NOW))
        monkeypatch.setattr(scan_service, "time", Ticker())
        monkeypatch.setattr(derivation, "OpenRouterClient", ModelClient)
        monkeypatch.setattr(credentials, "openrouter_api_key", lambda: "synthetic-key")
        # The scan's stages are services (phase 3); each stand-in records its request.
        monkeypatch.setattr(pipeline, "harvest", self._harvest)
        monkeypatch.setattr(pipeline, "derive", self._command("derive"))
        monkeypatch.setattr(pipeline, "archive", self._command("archive"))
        monkeypatch.setattr(pipeline, "consolidate", self._command("consolidate"))

        # The board, replaced at its module boundary.
        monkeypatch.setattr(dashboard_context, "export_context", self._export_context)
        monkeypatch.setattr(dashboard_composition, "composition_key", self._composition_key)
        monkeypatch.setattr(dashboard_composition, "reuse_composition", self._reuse)
        monkeypatch.setattr(dashboard_composition, "compose", self._compose)
        monkeypatch.setattr(dashboard_timeline, "slice_starts", self._slice_starts)
        monkeypatch.setattr(dashboard_timeline, "slice_state",
                            lambda state, context: {**state, "at": context["now"]})
        monkeypatch.setattr(parents_board, "attach_lookahead",
                            lambda frame, context: {**frame, "ahead_days": context["days"]})
        monkeypatch.setattr(weather, "attach_weather", self._weather)
        monkeypatch.setattr(day_summaries, "summary_lookup", lambda snapshot: "summaries")
        monkeypatch.setattr(day_summaries, "attach_day_summaries",
                            lambda state, lookup: {**state, "day_summaries": lookup})
        monkeypatch.setattr(day_view, "lesson_lookup", lambda snapshot: "lessons")
        monkeypatch.setattr(day_view, "attach_day_view", self._day_view)
        monkeypatch.setattr(dashboard_renderer, "validate", lambda state: None)
        monkeypatch.setattr(live_subjects, "load_live_messages", self._live_messages)
        monkeypatch.setattr(display_store, "make_timeline", self._make_timeline)
        self.board_status = board_status

        self._seed(tmp_path)

    def _seed(self, tmp_path):
        seed = tmp_path / "seed" / "school-restricted.db"
        with SnapshotStore(seed):
            pass
        self.gcs.put("private", "state/restricted-v1/school-restricted.db", seed.read_bytes())
        self.gcs.put("private", "state/auth-state.json", b'{"cookies": []}')
        self.gcs.put("private", "dashboard/event-confirmations.json", b'{"confirmed": []}')
        self.gcs.put("private", "dashboard/composition-cache.json", json.dumps({"weeks": {
            "key-2026-11-09": {"state": {"schema_version": 2, "composed_for": "cached"},
                               "audit": {"facts": []}},
        }}))
        self.gcs.put("delivery", "completion-observations/synthetic-1.json", json.dumps(
            {"completed_stable_keys": ["synthetic-key"], "observed_at": "2026-11-11T18:00:00Z"}))
        self.gcs.put("delivery", "receipts/synthetic-1.json", json.dumps(
            {"applied_stable_keys": ["synthetic-key"], "finished_at": "2026-11-11T18:05:00.123Z"}))
        self.gcs.put("control", "device-status/synthetic-board.json", json.dumps(
            {"received_at": "2026-11-12T05:30:00Z",
             "report": {"battery_present": True, "battery_percent": 81.6}}))
        # The household's two files: the product holds no school or children of its own.
        self.gcs.put("private", "state/school.toml", school_toml(SCHOOL))
        self.gcs.put("private", "state/profile.toml", profile_toml(CHILDREN))

    # Commands

    async def _harvest(self, request):
        self.commands.append(("harvest", asdict(request)))
        return StageResult(self.exit_codes["harvest"], {"status": "complete", "records": 12})

    def _command(self, name):
        def run(request):
            values = asdict(request)
            stage = "archive_plan" if name == "archive" and values["dry_run"] else name
            self.commands.append((stage, values))
            if name == "derive":
                output = dict(self.derive_output)
            elif name == "consolidate":
                output = {
                    "status": "complete", "input_count": 5, "output_count": 4,
                    "reduction_count": 1, "dropped_group_count": 0, "unknown_id_count": 0,
                    "repeated_id_count": 0, "omitted_id_count": 0,
                    "items": [{"title": "private detail"}],
                }
            else:
                output = {"dry_run": values["dry_run"], "decisions": []}
            return StageResult(self.exit_codes[name], output)
        return run

    # Board stand-ins

    def _export_context(self, snapshot, *, now, message_mode, confirmations, days=None):
        self.calls.append(("context", now.isoformat(), days, message_mode, confirmations))
        return {
            "now": now.isoformat(),
            "freshness": {"expires_at": EXPIRES.isoformat(), "status": "fresh"},
            "children": ["Alfa", "Beta"],
            "days": days,
        }

    def _composition_key(self, context, model):
        from skoleklar.dashboard_timeline import school_week
        return f"key-{school_week(datetime.fromisoformat(context['now'])).isoformat()}"

    def _reuse(self, context, cached, rejected=None):
        if self.reject_cached:
            rejected.append("weekend activities must use the fixed weekend area")
            return None
        return cached["state"], cached["audit"]

    def _compose(self, context, client):
        self.calls.append(("compose", context["now"]))
        return {"schema_version": 2, "composed_for": context["now"]}, {"facts": []}

    def _slice_starts(self, now, until):
        self.calls.append(("slices", now.isoformat(), until.isoformat()))
        return list(SLICES)

    def _weather(self, state, now, *, fetch=None):
        assert callable(fetch)
        if now.weekday() == 4 and now.hour >= 15:
            return {**state, "weather": {"verdict": "dry", "days": ["2026-11-14", "2026-11-15"]}}
        return {**state, "day_weather": [now.date().isoformat()]}

    def _day_view(self, state, lookup):
        if state["at"] != NOW.isoformat():
            return state
        return {**state, "day_view": {"date": "2026-11-12", "children": [
            {"lessons": ["Dansk", "Matematik", "Musik"]}, {"lessons": ["Engelsk", "Idræt"]}]}}

    def _live_messages(self, children, *, topics=None):
        self.calls.append(("live", children, topics))
        return []

    def _make_timeline(self, frames, refresh_id, *, live_messages=None):
        self.frames = frames
        self.calls.append(("timeline", refresh_id, len(frames), live_messages))
        return {"schema_version": 1, "version": refresh_id, "frame_count": len(frames)}

    # Running

    def run(self, caplog, entry=None):
        caplog.clear()
        with caplog.at_level(logging.INFO, logger="agskoleintra.cloud_job"):
            try:
                self.result = (entry or cloud_job.run_cloud_sync)()
                self.error = None
            except BaseException as error:  # SystemExit from main() included
                self.result = None
                self.error = error
        self.lines = [record.getMessage() for record in caplog.records
                      if record.name == "agskoleintra.cloud_job"]
        self.events = [json.loads(line) for line in self.lines]
        assert_event_shapes(self.events)
        return self


def assert_event_shapes(events: list[dict]) -> None:
    for event in events:
        name = event["event"]
        assert name in EVENT_FIELDS, f"unpinned event {name}"
        required, optional = EVENT_FIELDS[name]
        fields = [key for key in event if key != "event"]
        # Required fields come first, in order, then any optional ones.
        assert fields[: len(required)] == list(required), name
        assert set(fields[len(required):]) <= optional, name


def names(events):
    return [event["event"] if "stage" not in event else f"{event['event']}:{event['stage']}"
            for event in events]


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("network access in a cloud scan test")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


@pytest.fixture
def scan(monkeypatch, tmp_path):
    return Scan(monkeypatch, tmp_path)


# The complete run

MANIFEST_ID = "20261112T060000Z-541137335942"


def complete_run_events(check_count: int) -> list[tuple[str, dict]]:
    stage = lambda name, **summary: [  # noqa: E731
        ("stage_started", {"stage": name}),
        ("stage_completed", {"stage": name, "duration_seconds": 0.25, **summary}),
    ]
    return [
        ("delivery_state_ingested", {"completed_item_count": 0, "delivered_item_count": 0}),
        *stage("harvest"),
        *stage("derive", candidate_count=3, failures=0),
        *stage("archive_plan"),
        *stage("archive"),
        *stage("consolidate", input_count=5, output_count=4, reduction_count=1,
               dropped_group_count=0, unknown_id_count=0, repeated_id_count=0,
               omitted_id_count=0),
        *stage("day_summaries", candidate_count=0, reused_count=0, generated_count=0,
               failed_count=0, skipped_count=0),
        *stage("review_checks", flagged=0, reused=0, rule=0, model=0, unclear=0,
               unchecked=0, model_failed=0),
        ("invariants_passed", {"check_count": check_count, "duration_seconds": 0.25}),
        ("composition_cache", {"week": "2026-11-09", "result": "reused"}),
        ("day_weather_attached", {"day_count": 1}),
        ("day_view_attached", {"date": "2026-11-12", "lesson_count": 5}),
        ("day_weather_attached", {"day_count": 1}),
        ("composition_cache", {"week": "2026-11-16", "result": "miss"}),
        ("weather_attached", {"verdict": "dry", "day_count": 2}),
        ("dashboard_timeline", {"frame_count": 3, "week_count": 2, "reused_week_count": 1,
                                "fallback_week_count": 0}),
        ("sync_completed", {
            "status": "complete",
            "refresh_id": REFRESH_ID,
            "finished_at": "2026-11-12T06:00:00+00:00",
            "outputs": {
                "iphone": {"status": "published", "artifact_id": MANIFEST_ID},
                "dashboard": {"status": "published", "artifact_id": REFRESH_ID},
            },
        }),
    ]


def test_complete_scan_logs_these_events_byte_for_byte(scan, caplog) -> None:
    from skoleklar import invariants

    scan.run(caplog)

    assert scan.error is None
    expected = [
        json.dumps({"event": name, **fields}, separators=(",", ":"))
        for name, fields in complete_run_events(len(invariants.CHECKS))
    ]
    assert scan.lines == expected


def test_complete_scan_runs_the_stages_in_this_order(scan, caplog) -> None:
    scan.run(caplog)

    assert [stage for stage, _ in scan.commands] == [
        "harvest", "derive", "archive_plan", "archive", "consolidate",
    ]
    assert names(scan.events)[:17] == [
        "delivery_state_ingested",
        "stage_started:harvest", "stage_completed:harvest",
        "stage_started:derive", "stage_completed:derive",
        "stage_started:archive_plan", "stage_completed:archive_plan",
        "stage_started:archive", "stage_completed:archive",
        "stage_started:consolidate", "stage_completed:consolidate",
        "stage_started:day_summaries", "stage_completed:day_summaries",
        "stage_started:review_checks", "stage_completed:review_checks",
        "invariants_passed",
        "composition_cache",
    ]
    # The state upload follows the invariant checks; the phone manifest follows
    # the upload; the board is written after the phone; the run summary last.
    trail = scan.gcs.trail
    upload = trail.index("private upload state/restricted-v1/school-restricted.db if_generation_match=101")
    phone = next(i for i, entry in enumerate(trail) if entry.startswith("delivery write manifests/"))
    board = trail.index("display write current.json if_generation_match=0")
    summary = trail.index("control write latest-run.json if_generation_match=None")
    assert upload < phone < board < summary


def test_complete_scan_reads_and_writes_these_objects(scan, caplog) -> None:
    scan.run(caplog)

    assert scan.gcs.trail == [
        # The lock, held for the whole run.
        "control write pipeline-lock.json if_generation_match=0",
        # Restricted state and the shared session.
        "private reload state/restricted-v1/school-restricted.db",
        "private download state/restricted-v1/school-restricted.db",
        "private reload state/auth-state.json",
        "private download state/auth-state.json",
        # The household's school and profile (required since 14 November) and its
        # meaning (phase 4, optional); never uploaded.
        "private reload state/school.toml",
        "private download state/school.toml",
        "private reload state/profile.toml",
        "private download state/profile.toml",
        "private reload state/skoleklar.toml",
        "private reload state/triage.toml",
        # The household's own house pictures, when it keeps any; never uploaded.
        "private list state/artwork/",
        # What the phone completed and received since the last run.
        "delivery list completion-observations/",
        "delivery read completion-observations/synthetic-1.json",
        "delivery list receipts/",
        "delivery read receipts/synthetic-1.json",
        # Back up only after the invariant checks, each guarded by its generation.
        "private upload state/restricted-v1/school-restricted.db if_generation_match=101",
        "private upload state/auth-state.json if_generation_match=102",
        # The phone: a create-only manifest, then the pointer.
        f"delivery write manifests/{MANIFEST_ID}.json if_generation_match=0",
        "delivery write current.json if_generation_match=None",
        # The board.
        "private read dashboard/event-confirmations.json",
        "private read dashboard/composition-cache.json",
        "private write dashboard/current-state.json",
        "private write dashboard/current-audit.json",
        "private write dashboard/composition-cache.json",
        "control list device-status/",
        "control read device-status/synthetic-board.json",
        "display reload-bucket ",
        "display reload current.json",
        "display write current.json if_generation_match=0",
        # The run summary, then the lock is released.
        "control write latest-run.json if_generation_match=None",
        "control delete pipeline-lock.json if_generation_match=110",  # the lock's own generation
    ]
    assert ("control", "pipeline-lock.json") not in scan.gcs.objects
    # The upload replaced the generation the scan downloaded.
    assert scan.gcs.objects[("private", "state/restricted-v1/school-restricted.db")][1] > 101


def test_complete_scan_passes_these_requests_to_each_service(scan, caplog) -> None:
    scan.run(caplog)

    commands = {stage: values for stage, values in scan.commands}
    for values in commands.values():
        assert Path(values.pop("db")).name == "school-restricted.db"
    assert commands == {
        "harvest": {
            "mode": "daily", "calendar_past_weeks": None, "calendar_future_weeks": None,
            "weekly_plan_weeks": None, "message_pages": None, "frontpage_pages": None,
            "auto_login": True, "auto_login_timeout": 2, "message_mode": "events-only",
            "message_processor": "zdr", "model": "synthetic/model-1",
            # The request's two other fields, at the values the command read with getattr.
            "message_concurrency": 2, "progress": "auto",
        },
        "derive": {
            "run_id": None, "source": None, "limit": None, "dry_run": False,
            "model": "synthetic/model-1", "backfill_since": None, "message_mode": "events-only",
            # The board's facts made under an older contract are re-derived (skoleklar.fact).
            "migrate": True, "as_of": "2026-11-12", "migrate_limit": 40,
        },
        "archive_plan": {
            "as_of": "2026-11-12", "model": "synthetic/model-1", "dry_run": True,
            "no_agent_review": False, "message_mode": "events-only",
        },
        "archive": {
            "as_of": "2026-11-12", "model": "synthetic/model-1", "dry_run": False,
            "no_agent_review": False, "message_mode": "events-only",
        },
        "consolidate": {
            "as_of": "2026-11-12", "limit": 200, "model": "synthetic/model-1",
            "dry_run": False, "message_mode": "events-only",
        },
    }
    # One client for day summaries, one for review checks, one for the board.
    assert ModelClient.created == [{"api_key": "synthetic-key", "model": "synthetic/model-1"}] * 3


def test_complete_scan_writes_this_run_summary_and_board(scan, caplog) -> None:
    scan.run(caplog)

    summary = scan.gcs.json("control", "latest-run.json")
    assert summary == {
        "status": "complete",
        "refresh_id": REFRESH_ID,
        "finished_at": "2026-11-12T06:00:00+00:00",
        "outputs": {
            "iphone": {"status": "published", "artifact_id": MANIFEST_ID},
            "dashboard": {"status": "published", "artifact_id": REFRESH_ID},
        },
    }
    assert scan.result == summary
    assert {key: value for key, value in scan.events[-1].items() if key != "event"} == summary
    pointer = scan.gcs.json("delivery", "current.json")
    assert pointer == {
        "schema_version": 1,
        "manifest_id": MANIFEST_ID,
        "object": f"manifests/{MANIFEST_ID}.json",
        "updated_at": "2026-11-12T06:00:00Z",
    }
    manifest = scan.gcs.json("delivery", f"manifests/{MANIFEST_ID}.json")
    assert manifest["coverage"]["mode"] == "events-only"
    assert manifest["expires_at"] == "2026-11-13T18:00:00Z"

    # The board: contexts for the first moment and for each slice, then the
    # lookahead context for each frame.
    contexts = [call for call in scan.calls if call[0] == "context"]
    assert [(at, days) for _, at, days, _, _ in contexts] == [
        (NOW.isoformat(), None),
        (NOW.isoformat(), 18),
        (SLICES[1].isoformat(), None),
        (SLICES[1].isoformat(), 18),
        (SLICES[2].isoformat(), None),
        (SLICES[2].isoformat(), 18),
    ]
    assert {call[3] for call in contexts} == {"events-only"}
    assert {json.dumps(call[4]) for call in contexts} == {'{"confirmed": []}'}
    # The timeline runs until the context expires, if that is before 36 hours.
    assert ("slices", NOW.isoformat(), EXPIRES.isoformat()) in scan.calls
    assert [call for call in scan.calls if call[0] == "compose"] == [
        ("compose", SLICES[2].isoformat())
    ]
    assert ("live", ["Alfa", "Beta"], None) in scan.calls
    assert ("timeline", REFRESH_ID, 3, []) in scan.calls
    first, second, third = (state for _, state in scan.frames)
    assert first == {
        "schema_version": 2, "composed_for": "cached", "at": NOW.isoformat(),
        "day_weather": ["2026-11-12"], "day_summaries": "summaries",
        "day_view": {"date": "2026-11-12", "children": [
            {"lessons": ["Dansk", "Matematik", "Musik"]}, {"lessons": ["Engelsk", "Idræt"]}]},
        "ahead_days": 18, "device": {"battery_percent": 82},
        # The earlier stages' health reaches every frame (skoleklar.health).
        "health": {"composition": {"status": "ok"}, "day_summaries": {"status": "ok"},
                   "review_checks": {"status": "ok"}, "facts": {"status": "ok"}},
    }
    assert "day_view" not in second
    assert third["weather"]["verdict"] == "dry"
    assert [at for at, _ in scan.frames] == SLICES
    cache = scan.gcs.json("private", "dashboard/composition-cache.json")
    assert sorted(cache["weeks"]) == ["key-2026-11-09", "key-2026-11-16"]
    assert scan.gcs.json("private", "dashboard/current-state.json") == {
        key: value for key, value in first.items() if key != "device"
    }
    assert json.loads(scan.gcs.objects[("display", "current.json")][0]) == {
        "schema_version": 1, "version": REFRESH_ID, "frame_count": 3
    }


# Failure branches


def test_failed_stage_stops_the_scan_before_any_upload(scan, caplog) -> None:
    scan.exit_codes["derive"] = 1
    scan.derive_output = {
        "candidate_count": 2,
        "failures": 2,
        "error": "OpenRouter Chat Completions API returned 503: private detail",
    }
    scan.monkeypatch.setattr(logging, "basicConfig", lambda **kwargs: None)
    scan.run(caplog, cloud_job.main)

    assert isinstance(scan.error, SystemExit) and scan.error.code == 1
    assert [stage for stage, _ in scan.commands] == ["harvest", "derive"]
    assert scan.lines[-2:] == [
        '{"event":"stage_failed","stage":"derive","exit_code":1,'
        '"failure_code":"provider_http_503","duration_seconds":0.25,'
        '"candidate_count":2,"failures":2}',
        '{"event":"sync_failed","error_type":"RuntimeError"}',
    ]
    assert "private detail" not in caplog.text
    assert not any(" upload " in entry for entry in scan.gcs.trail)
    assert not any(entry.startswith(("delivery write", "display write")) for entry in scan.gcs.trail)
    latest = scan.gcs.json("control", "latest-run.json")
    assert latest == {"status": "failed", "error_type": "RuntimeError",
                      "finished_at": "2026-11-12T06:00:00+00:00", "outputs": {}}
    assert scan.gcs.trail[-1].startswith("control delete pipeline-lock.json")


def test_held_lock_refuses_a_second_scan(scan, caplog) -> None:
    scan.gcs.put("control", "pipeline-lock.json", b'{"execution": "other"}')
    scan.run(caplog)

    assert isinstance(scan.error, RuntimeError)
    assert str(scan.error) == "A refresh already owns the pipeline lock"
    assert scan.commands == []
    assert scan.events == []
    assert scan.gcs.trail == ["control write pipeline-lock.json if_generation_match=0"]


def test_day_summary_and_review_check_failures_never_stop_the_scan(scan, caplog) -> None:
    from skoleklar import day_summaries
    from skoleklar.triage import review_checks

    def fail_summaries(*args, **kwargs):
        raise RuntimeError("private detail")

    def fail_reviews(*args, **kwargs):
        raise ValueError("private detail")

    scan.monkeypatch.setattr(day_summaries, "generate_day_summaries", fail_summaries)
    scan.monkeypatch.setattr(review_checks, "check_reviews", fail_reviews)
    scan.run(caplog)

    assert scan.result["status"] == "complete"
    assert scan.lines[11:15] == [
        '{"event":"stage_started","stage":"day_summaries"}',
        '{"event":"day_summaries_failed","error_type":"RuntimeError","duration_seconds":0.25}',
        '{"event":"stage_started","stage":"review_checks"}',
        '{"event":"review_checks_failed","error_type":"ValueError","duration_seconds":0.25}',
    ]
    assert scan.events[15]["event"] == "invariants_passed"
    assert "private detail" not in caplog.text


def test_day_summaries_report_their_failures_when_there_are_any(scan, caplog) -> None:
    from skoleklar import day_summaries

    def partly(*args, **kwargs):
        return {"candidate_count": 2, "reused_count": 0, "generated_count": 1, "failed_count": 1,
                "skipped_count": 0, "failures": ["provider_http_429"], "plans": ["private"]}

    scan.monkeypatch.setattr(day_summaries, "generate_day_summaries", partly)
    scan.run(caplog)

    assert scan.lines[12] == (
        '{"event":"stage_completed","stage":"day_summaries","duration_seconds":0.25,'
        '"candidate_count":2,"reused_count":0,"generated_count":1,"failed_count":1,'
        '"skipped_count":0,"failures":["provider_http_429"]}'
    )


def test_invariant_failure_stops_before_the_upload(scan, caplog) -> None:
    from skoleklar import invariants

    scan.monkeypatch.setattr(invariants, "check", lambda connection, integrity=True: [
        ("foreign keys hold", [("private row",)]),
        ("forms are at their current version", [("private row",)]),
    ])
    scan.run(caplog)

    assert isinstance(scan.error, RuntimeError)
    assert str(scan.error) == (
        "Invariant checks failed: foreign keys hold; forms are at their current version"
    )
    assert scan.lines[-1] == (
        '{"event":"invariants_failed","checks":["foreign keys hold",'
        '"forms are at their current version"],"duration_seconds":0.25}'
    )
    assert "private row" not in caplog.text
    assert not any(" upload " in entry for entry in scan.gcs.trail)
    assert scan.gcs.json("control", "latest-run.json")["status"] == "failed"


def test_invariants_run_on_the_scanned_database_without_integrity_check(scan, caplog) -> None:
    from skoleklar import invariants

    seen = []

    def record(connection, integrity=True):
        seen.append((connection.execute("PRAGMA database_list").fetchone()[2], integrity))
        return []

    scan.monkeypatch.setattr(invariants, "check", record)
    scan.run(caplog)

    assert len(seen) == 1
    path, integrity = seen[0]
    assert Path(path).name == "school-restricted.db"
    assert integrity is False


def test_board_extras_failures_are_logged_and_never_stop_the_board(scan, caplog) -> None:
    from skoleklar import board_status, day_summaries, day_view, parents_board, weather

    def fail(*args, **kwargs):
        raise KeyError("private detail")

    for module, name in [(weather, "attach_weather"), (day_summaries, "attach_day_summaries"),
                         (day_view, "attach_day_view"), (parents_board, "attach_lookahead"),
                         (board_status, "latest_record")]:
        scan.monkeypatch.setattr(module, name, fail)
    scan.gcs.put("private", "dashboard/composition-cache.json", b"not json")
    scan.run(caplog)

    assert scan.result["outputs"]["dashboard"]["status"] == "published"
    board = scan.lines[16:]
    per_frame = [
        '{"event":"weather_failed","error_type":"KeyError"}',
        '{"event":"lookahead_failed","error_type":"KeyError"}',
    ]
    assert board == [
        '{"event":"composition_cache_unavailable","error_type":"JSONDecodeError"}',
        '{"event":"composition_cache","week":"2026-11-09","result":"miss"}',
        '{"event":"weather_failed","error_type":"KeyError"}',
        # Each extra reports its first failure only.
        '{"event":"day_summaries_unattached","error_type":"KeyError"}',
        '{"event":"day_view_unattached","error_type":"KeyError"}',
        '{"event":"lookahead_failed","error_type":"KeyError"}',
        *per_frame,
        '{"event":"composition_cache","week":"2026-11-16","result":"miss"}',
        *per_frame,
        '{"event":"dashboard_timeline","frame_count":3,"week_count":2,"reused_week_count":0,'
        '"fallback_week_count":0}',
        '{"event":"device_battery_unavailable","error_type":"KeyError"}',
        scan.lines[-1],
    ]
    assert "private detail" not in caplog.text
    # The board says what is missing instead of only logging it.
    for _, state in scan.frames:
        assert {stage: entry["status"] for stage, entry in state["health"].items()} == {
            "composition": "ok", "facts": "ok",
            "day_summaries": "degraded", "review_checks": "ok", "composition_cache": "degraded",
            "weather": "degraded", "day_view": "degraded", "lookahead": "degraded", "battery": "degraded"}
        assert state["health"]["weather"] == {"status": "degraded", "reason": "key_error"}


def test_day_view_off_attaches_no_children_view(scan, caplog) -> None:
    scan.monkeypatch.setenv("AG_DAY_VIEW", "off")
    scan.run(caplog)

    assert "day_view_attached" not in names(scan.events)
    assert all("day_view" not in state for _, state in scan.frames)


def test_rejected_cache_and_failed_composition_fall_back_to_a_plain_board(scan, caplog) -> None:
    from skoleklar import dashboard_build, dashboard_composition

    attempts = ["weekend activities must use the fixed weekend area"] * 6

    def compose(context, client):
        if context["now"] == NOW.isoformat():
            raise dashboard_composition.CompositionFailed(
                "Dashboard composition failed validation: ValueError: private detail", attempts)
        return {"schema_version": 2, "composed_for": context["now"]}, {"facts": []}

    scan.reject_cached = True
    scan.monkeypatch.setattr(dashboard_composition, "compose", compose)
    scan.monkeypatch.setattr(dashboard_build, "fallback_composition",
                             lambda context: ({"schema_version": 2, "composed_for": "plain"},
                                              {"facts": []}))
    scan.run(caplog)

    board = [event for event in scan.events if event["event"].startswith(("composition", "dashboard"))]
    assert board == [
        {"event": "composition_cache", "week": "2026-11-09", "result": "rejected",
         "reason": "weekend activities must use the fixed weekend area"},
        {"event": "composition_fallback", "week": "2026-11-09", "error_type": "CompositionFailed",
         "failure_code": "composition_validation", "attempts": attempts},
        {"event": "composition_cache", "week": "2026-11-16", "result": "miss"},
        {"event": "dashboard_timeline", "frame_count": 3, "week_count": 2, "reused_week_count": 0,
         "fallback_week_count": 1},
    ]
    assert scan.result["status"] == "complete"
    assert scan.result["outputs"]["dashboard"] == {
        "status": "published", "artifact_id": REFRESH_ID, "fallback_weeks": 1,
    }
    # A fallback week is not cached, so the next scan asks the model again.
    cache = scan.gcs.json("private", "dashboard/composition-cache.json")
    assert sorted(cache["weeks"]) == ["key-2026-11-16"]
    assert "private detail" not in caplog.text


def test_failed_fallback_fails_the_board_and_keeps_the_phone(scan, caplog) -> None:
    from skoleklar import dashboard_build, dashboard_composition

    def compose(context, client):
        raise dashboard_composition.CompositionFailed(
            "Dashboard composition failed validation: ValueError: "
            "dashboard dates must follow the selected school week", ["private detail"])

    def fallback(context):
        raise TypeError("private detail")

    scan.reject_cached = True
    scan.monkeypatch.setattr(dashboard_composition, "compose", compose)
    scan.monkeypatch.setattr(dashboard_build, "fallback_composition", fallback)
    scan.run(caplog)

    assert scan.lines[-3:] == [
        '{"event":"composition_fallback_failed","week":"2026-11-09","error_type":"TypeError"}',
        '{"event":"dashboard_failed","failure_stage":"compose","error_type":"CompositionFailed",'
        '"failure_code":"wrong_school_week","attempts":["private detail"]}',
        scan.lines[-1],
    ]
    assert scan.events[-1]["status"] == "partial"
    assert scan.events[-1]["outputs"] == {
        "iphone": {"status": "published", "artifact_id": MANIFEST_ID},
        "dashboard": {"status": "failed", "error_type": "CompositionFailed",
                      "failure_stage": "compose"},
    }
    assert not any(entry.startswith("display write") for entry in scan.gcs.trail)
    assert ("delivery", "current.json") in scan.gcs.objects


def test_phone_failure_still_publishes_the_board(scan, caplog) -> None:
    def fail(*args, **kwargs):
        raise ValueError("private detail")

    scan.monkeypatch.setattr(scan_service, "build_delivery_manifest", fail)
    scan.run(caplog)

    assert scan.result["status"] == "partial"
    assert scan.result["outputs"] == {
        "iphone": {"status": "failed", "error_type": "ValueError"},
        "dashboard": {"status": "published", "artifact_id": REFRESH_ID},
    }
    assert "private detail" not in caplog.text


def test_without_a_display_bucket_the_board_is_disabled(scan, caplog) -> None:
    scan.monkeypatch.delenv("AG_DISPLAY_BUCKET")
    scan.run(caplog)

    assert scan.result["outputs"]["dashboard"] == {"status": "disabled"}
    assert names(scan.events)[-2:] == ["invariants_passed", "sync_completed"]
    assert ModelClient.created == [{"api_key": "synthetic-key", "model": "synthetic/model-1"}] * 2


def test_legacy_message_mode_is_refused_before_any_work(scan, caplog) -> None:
    scan.monkeypatch.setenv("AG_SCHOOL_MESSAGE_MODE", "legacy")
    scan.monkeypatch.setenv("AG_SCHOOL_MESSAGE_PROCESSOR", "disabled")
    scan.run(caplog)

    assert isinstance(scan.error, ValueError)
    assert scan.commands == []
    assert scan.events == []


# The household's profile and school (phase 1 of the architecture review)

def test_profile_files_in_the_bucket_reach_the_scan(scan, caplog) -> None:
    from skoleklar import household as site_profile

    scan.gcs.put("private", "state/school.toml", '[site]\nhost = "eksempelskolen.m.skoleintra.dk"\n')
    scan.gcs.put("private", "state/profile.toml",
                 '[[children]]\nstudent_id = 9101\nname = "Alfa"\ncalendar_class_name = "001"\n')
    seen = []
    harvest = pipeline.harvest

    async def harvest_with_profile(args):
        seen.append(site_profile.load_site_profile())
        return await harvest(args)

    scan.monkeypatch.setattr(pipeline, "harvest", harvest_with_profile)
    scan.run(caplog)

    assert scan.error is None and scan.result["status"] == "complete"
    [profile] = seen
    assert profile.sources == ("school.toml", "profile.toml")
    assert profile.login_url == "https://eksempelskolen.m.skoleintra.dk/parent/9101/Alfa/learningplan/student"
    assert "private download state/school.toml" in scan.gcs.trail
    assert "private download state/profile.toml" in scan.gcs.trail
    assert not any(entry.startswith(("private upload state/school", "private upload state/profile"))
                   for entry in scan.gcs.trail)


def test_a_broken_profile_stops_the_scan_before_any_work(scan, caplog) -> None:
    scan.gcs.put("private", "state/profile.toml", "children = []\n")
    scan.monkeypatch.setattr(logging, "basicConfig", lambda **kwargs: None)
    scan.run(caplog, cloud_job.main)

    assert isinstance(scan.error, SystemExit) and scan.error.code == 1
    assert scan.commands == []
    assert scan.lines == ['{"event":"sync_failed","error_type":"ProfileError"}']
    assert not any(" upload " in entry for entry in scan.gcs.trail)
    assert scan.gcs.json("control", "latest-run.json")["error_type"] == "ProfileError"


def test_the_households_meaning_in_the_bucket_reaches_the_bookings(scan, caplog) -> None:
    from skoleklar import meaning

    scan.gcs.put("private", "state/skoleklar.toml", "[meaning.bookings]\ndefault_minutes = 20\n")
    seen = []
    load = meaning.load_meaning

    def load_and_keep(home):
        seen.append(load(home))
        return seen[-1]

    scan.monkeypatch.setattr(meaning, "load_meaning", load_and_keep)
    scan.monkeypatch.setattr(pipeline, "load_meaning", load_and_keep)
    scan.monkeypatch.setattr(scan_service, "load_meaning", load_and_keep)
    scan.run(caplog)

    assert scan.error is None and scan.result["status"] == "complete"
    assert seen and {value.default_minutes for value in seen} == {20}
    assert "private download state/skoleklar.toml" in scan.gcs.trail
    assert not any(entry.startswith("private upload state/skoleklar") for entry in scan.gcs.trail)


def test_a_broken_meaning_stops_the_scan_before_any_work(scan, caplog) -> None:
    scan.gcs.put("private", "state/skoleklar.toml", "[meaning.bookings]\nround = \"first_notice\"\n")
    scan.monkeypatch.setattr(logging, "basicConfig", lambda **kwargs: None)
    scan.run(caplog, cloud_job.main)

    assert isinstance(scan.error, SystemExit) and scan.error.code == 1
    assert scan.commands == []
    assert scan.lines == ['{"event":"sync_failed","error_type":"ProfileError"}']
    assert not any(" upload " in entry for entry in scan.gcs.trail)


def test_the_scan_works_in_its_own_directory_and_leaves_the_environment_alone(scan, caplog, tmp_path) -> None:
    import os

    from skolekit import state as settings

    seen = []
    harvest = pipeline.harvest

    async def harvest_in_place(args):
        seen.append((Path(args.db).parent, settings.state_dir(), os.environ["AG_SCHOOL_HOME"]))
        return await harvest(args)

    scan.monkeypatch.setattr(pipeline, "harvest", harvest_in_place)
    scan.run(caplog)

    [(working, during, environment)] = seen
    assert during == working != tmp_path / "home"
    assert environment == str(tmp_path / "home")
    assert settings.state_dir() == (tmp_path / "home").resolve()


# The event list itself


def test_every_event_the_job_can_log_is_pinned() -> None:
    logged = set(re.findall(r'_event\(\s*"([a-z_]+)"',
                            "".join(source.read_text(encoding="utf-8") for source in SOURCES)))
    # `stage_started` / `stage_completed` / `stage_failed` come from `_run_quietly`
    # and the two optional stages; all are literal calls.
    assert logged == set(EVENT_FIELDS)
