import json
import re
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from skoleklar import dashboard_renderer, display_store, scan_log
from skoleklar.board_status import render_page
from skoleklar.browser_api import create_app as browser_app
from skoleklar.delivery_store import DeliveryObjectNotFoundError, LocalDeliveryStore
from skoleklar.day_view import build_day_view
from skoleklar.parents_board_render import _status, status_html
from skoleklar.runtime import scan
from skoleklar.runtime.scan import _safe_failure_code
from tests_python.test_day_view import lesson
from tests_python.test_parents_board import heavy_week_state
from tests_python.test_refresh_control import Dispatcher, MemoryStore, RefreshController

NOW = datetime(2026, 11, 25, 20, 30, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _empty_log():
    scan_log.reset()
    yield
    scan_log.reset()


# --- What a failure is called --------------------------------------------------------------

def test_an_expired_model_key_is_named_from_the_harvests_message_events():
    output = {"status": "partial", "sources": [{"complete": True, "error": None}],
              "message_events": {"status": "partial", "failure_count": 3,
                                 "failure_reasons": {"http_401": 3}, "error": "private detail"}}
    assert _safe_failure_code(output) == "provider_http_401"


def test_a_stage_that_names_its_reason_keeps_it_and_a_broken_page_is_incomplete():
    assert _safe_failure_code({"failure_code": "school_login_failed"}) == "school_login_failed"
    assert _safe_failure_code({"failure_code": "Not A Code: private"}) == "command_failed"
    assert _safe_failure_code({"sources": [{"complete": False, "error": "TypeError: x"}]}) == "source_incomplete"


# --- The log ---------------------------------------------------------------------------------

def failed_run(store, at):
    scan_log.reset()
    scan_log.note("stage_started", {"stage": "harvest"}, at=at)
    scan_log.note("stage_failed", {"stage": "harvest", "exit_code": 2, "failure_code": "provider_http_401",
                                   "nested": {"private": "never kept"}}, at=at)
    return scan_log.append_run(store, {"status": "failed", "error_type": "RuntimeError",
                                       "finished_at": at.isoformat(), "outputs": {}})


def test_a_run_keeps_its_safe_events_its_failure_and_only_the_newest_runs(tmp_path, monkeypatch):
    store = LocalDeliveryStore(tmp_path)
    assert scan_log.history(store) == [] and scan_log.history(None) == []
    monkeypatch.setattr(scan_log, "MAX_RUNS", 3)
    for hours in range(5):
        entry = failed_run(store, NOW - timedelta(hours=4 - hours))
    assert entry["critical"] and entry["failure"] == {"stage": "harvest", "code": "provider_http_401"}
    assert "nested" not in entry["events"][1] and entry["events"][1]["exit_code"] == 2
    assert len(scan_log.history(store)) == 3


def test_a_published_board_is_not_critical_but_a_failed_board_output_is():
    assert not scan_log.is_critical({"status": "complete", "outputs": {"dashboard": {"status": "published"}}})
    assert not scan_log.is_critical({"status": "partial", "outputs": {"iphone": {"status": "failed"},
                                                                      "dashboard": {"status": "published"}}})
    summary = {"status": "partial", "outputs": {"dashboard": {"status": "failed", "error_type": "ValueError"}}}
    assert scan_log.is_critical(summary)
    assert scan_log.failure(summary, []) == {"stage": "dashboard", "code": "value_error"}


def test_the_page_folds_the_log_and_says_why_the_last_scans_failed(tmp_path):
    store = LocalDeliveryStore(tmp_path)
    for hours in (2, 1):
        failed_run(store, NOW - timedelta(hours=hours))
    page = render_page(None, NOW, scans=scan_log.history(store))
    section = page[page.index("<details class='scanlog"):]
    assert "Last 2 scans failed" in section and "OpenRouter rejected the API key" in section
    assert "stage_failed" in section and "failure_code=provider_http_401" in section
    assert "<details class='scanlog state-error'>" in page and " open" not in section.split(">")[0]
    assert "No scans recorded yet" in render_page(None, NOW)


def test_the_page_escapes_what_the_events_say(tmp_path):
    scan_log.note("stage_failed", {"stage": "<b>x</b>"}, at=NOW)
    page = render_page(None, NOW, scans=[{"finished_at": NOW.isoformat(), "status": "failed",
                                          "critical": True, "events": scan_log.events()}])
    assert "<b>x</b>" not in page and "&lt;b&gt;x&lt;/b&gt;" in page


def test_browser_draws_the_scan_log_from_the_control_store(tmp_path):
    statuses = LocalDeliveryStore(tmp_path / "control")
    failed_run(statuses, NOW)
    browser = TestClient(browser_app(store=LocalDeliveryStore(tmp_path / "display"),
                                     controller=RefreshController(MemoryStore(), Dispatcher()),
                                     verifier=lambda token, audience: {"email": "viewer@example.com"},
                                     audience="configured", viewers=["viewer@example.com"], owners=[],
                                     origin="https://testserver", status_store=statuses),
                         base_url="https://testserver")
    page = browser.get("/board-status", headers={"X-Goog-IAP-JWT-Assertion": "viewer"})
    assert page.status_code == 200 and "Scan log" in page.text and "Last scan failed" in page.text


# --- The warning on the board ----------------------------------------------------------------

def test_a_failing_scan_is_said_first_and_in_bold_on_the_board_and_in_the_browser():
    state = {**heavy_week_state(), "alert": {"code": "provider_http_401"}}
    dashboard_renderer.validate(state)
    assert _status(state).startswith("OPDATERING FEJLER · Opdateret")
    html = status_html(state)
    assert 'class="pb-alert"' in html and "stale" in html
    with pytest.raises(ValueError):
        dashboard_renderer.validate({**state, "alert": {"code": "Private text"}})


def test_the_triangle_is_drawn_in_yellow_and_ink_beside_the_status():
    plain = dashboard_renderer.render_png(heavy_week_state()).convert("RGB")
    alert = dashboard_renderer.render_png({**heavy_week_state(), "alert": {"code": "x"}}).convert("RGB")
    corner = (dashboard_renderer.PANEL_SPLIT_X, 0, 800, 44)  # the white header right of the dark side
    yellow = dashboard_renderer.PANEL_NOMINAL_RGB[0x2]
    assert yellow not in [colour for _, colour in plain.crop(corner).getcolors(1 << 16)]
    assert yellow in [colour for _, colour in alert.crop(corner).getcolors(1 << 16)]


def kids(state):
    """The same moment as the children's view, built as the scan builds it."""
    names = [child["name"] for child in state["hero"]["children"]]
    view = build_day_view(date.fromisoformat(state["hero"]["date"]), names, "I dag",
                          {name: [lesson("08:00", "08:45", "DAN", day=state["hero"]["date"])] for name in names})
    return {**state, "day_view": view}


def is_kids(frame):
    return "day-view" in re.search(r'<main class="([^"]*)"', frame["html"]).group(1)


def served(timeline, at):
    class Store:
        def read_json(self, name):
            return timeline
    return display_store.current_bundle(Store(), now=at)


def test_the_alert_keeps_the_timeline_switching_then_stays_on_the_parents_board():
    parents = heavy_week_state()
    frames = [(NOW - timedelta(hours=2), parents), (NOW + timedelta(hours=1), kids(parents))]
    ends = NOW + timedelta(hours=3)
    timeline = display_store.make_alert_timeline(frames, "alert-run", code="provider_http_401",
                                                 ends_at=ends, now=NOW)
    shown = [(is_kids(frame), frame["metadata"]["valid_from"]) for frame in timeline["frames"]]
    assert [view for view, _ in shown] == [False, True, False]
    assert [start for _, start in shown] == [NOW.isoformat(), (NOW + timedelta(hours=1)).isoformat(),
                                             ends.isoformat()]
    assert all('class="pb-alert"' in frame["html"] or "OPDATERING FEJLER" in frame["html"]
               for frame in timeline["frames"])
    later = served(timeline, NOW + timedelta(hours=20))
    assert later["metadata"]["valid_until"] == (NOW + timedelta(hours=display_store.ALERT_HOURS)).isoformat()


def test_an_alert_after_the_timeline_ran_out_is_the_parents_board_from_now():
    timeline = display_store.make_alert_timeline([(NOW - timedelta(hours=40), kids(heavy_week_state()))],
                                                 "alert-run", code="x", ends_at=NOW - timedelta(hours=4), now=NOW)
    assert len(timeline["frames"]) == 1
    frame = served(timeline, NOW + timedelta(hours=1))
    assert frame["metadata"]["version"].startswith("alert-run-") and not is_kids(frame)


class Runtime:
    def __init__(self, tmp_path, state, *, frames=None):
        self.control = LocalDeliveryStore(tmp_path / "control")
        self.displays = LocalDeliveryStore(tmp_path / "display")
        self.private = {}
        if state:
            self.private["current-state.json"] = json.dumps(state).encode()
        if frames:
            self.private["current-frames.json"] = json.dumps(frames).encode()

    def status_store(self):
        return self.control

    def display_store(self):
        return self.displays

    def read_private(self, name):
        return self.private.get(name)

    def write_private(self, name, text):
        self.private[name] = text.encode()


def test_a_failed_scan_records_itself_and_puts_the_alert_on_the_board(tmp_path):
    runtime = Runtime(tmp_path, heavy_week_state())
    scan._event("stage_failed", stage="harvest", exit_code=2, failure_code="provider_http_401")
    scan._record_scan_log(runtime, {"status": "failed", "error_type": "RuntimeError",
                                    "finished_at": NOW.isoformat(), "outputs": {}, "refresh_id": "run-7"})
    assert scan_log.history(runtime.control)[-1]["failure"]["code"] == "provider_http_401"
    published = runtime.displays.read_json("current.json")
    assert published["refresh_id"] == "alert-run-7" and len(published["frames"]) == 1
    assert [entry["event"] for entry in scan_log.events()][-1] == "alert_published"


def test_a_failed_scan_marks_the_kept_frames_of_the_last_timeline(tmp_path):
    now = datetime.now(UTC)
    state = heavy_week_state()
    frames = [[(now - timedelta(hours=1)).isoformat(), state], [(now + timedelta(hours=2)).isoformat(), kids(state)]]
    runtime = Runtime(tmp_path, state, frames={"expires_at": (now + timedelta(hours=5)).isoformat(), "frames": frames})
    scan._record_scan_log(runtime, {"status": "failed", "finished_at": now.isoformat(), "outputs": {}})
    assert len(runtime.displays.read_json("current.json")["frames"]) == 3


def test_a_published_timeline_keeps_its_frames_for_the_next_alert(tmp_path):
    runtime = Runtime(tmp_path, None)
    at = NOW
    scan._persist_frames(runtime, [(at, {"x": 1})], {"expires_at": "2026-11-27T00:00:00+00:00"})
    assert json.loads(runtime.private["current-frames.json"]) == {
        "expires_at": "2026-11-27T00:00:00+00:00", "frames": [[at.isoformat(), {"x": 1}]]}


def test_a_good_scan_leaves_the_board_alone_and_a_missing_state_never_fails_the_scan(tmp_path):
    runtime = Runtime(tmp_path, heavy_week_state())
    scan._record_scan_log(runtime, {"status": "complete", "finished_at": NOW.isoformat(),
                                    "outputs": {"dashboard": {"status": "published"}}})
    with pytest.raises(DeliveryObjectNotFoundError):
        runtime.displays.read_json("current.json")
    empty = Runtime(tmp_path / "empty", None)
    scan._record_scan_log(empty, {"status": "failed", "finished_at": NOW.isoformat(), "outputs": {}})
    last = scan_log.events()[-1]
    assert last["event"] == "alert_not_published" and last["reason"] == "no_display_or_state"
