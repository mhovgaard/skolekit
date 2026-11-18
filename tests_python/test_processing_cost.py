import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from skoleklar import processing_cost
from skoleklar.board_status import render_page
from skoleklar.browser_api import create_app as browser_app
from skoleklar.delivery_store import LocalDeliveryStore
from skoleklar.triage.derivation import OpenRouterClient, OpenRouterGenerationError
from skoleklar.triage.prompt_manifest import load_prompt_manifest
from tests_python.test_refresh_control import Dispatcher, MemoryStore, RefreshController

NOW = datetime(2026, 11, 18, 7, 0, tzinfo=UTC)


def client_answering(*answers):
    replies = iter(answers)

    def handle(request):
        return httpx.Response(200, json=next(replies))
    return OpenRouterClient(api_key="test", client=httpx.Client(transport=httpx.MockTransport(handle)))


def answer(content, **usage):
    return {"id": "r", "usage": usage, "choices": [{"finish_reason": "stop", "message": {"content": content}}]}


def test_every_billed_call_is_metered_by_stage_even_when_its_output_is_unusable():
    processing_cost.reset()
    client = client_answering(
        answer(json.dumps({"verdicts": []}), prompt_tokens=1000, completion_tokens=200, total_tokens=1200,
               cost=0.0042, prompt_tokens_details={"cached_tokens": 600}),
        answer("not json", prompt_tokens=500, completion_tokens=50, total_tokens=550, cost=0.001),
        answer(json.dumps({"verdicts": []}), prompt_tokens=10, completion_tokens=5, total_tokens=15),
    )
    manifest = load_prompt_manifest("review-check-v1")
    with processing_cost.stage("review_checks"):
        client.generate({}, manifest=manifest)
        with pytest.raises(OpenRouterGenerationError):
            client.generate({}, manifest=manifest)
    processing_cost.set_stage("dashboard")
    client.generate({}, manifest=manifest)

    totals = processing_cost.snapshot()
    assert totals["calls"] == 3 and totals["calls_without_cost"] == 1
    assert totals["cost_usd"] == pytest.approx(0.0052)
    assert (totals["prompt_tokens"], totals["completion_tokens"], totals["total_tokens"],
            totals["cached_tokens"]) == (1510, 255, 1765, 600)
    assert totals["stages"]["review_checks"]["calls"] == 2
    assert totals["stages"]["review_checks"]["cost_usd"] == pytest.approx(0.0052)
    assert totals["stages"]["dashboard"]["total_tokens"] == 15
    processing_cost.reset()
    assert processing_cost.snapshot()["calls"] == 0


def test_history_keeps_the_newest_runs(tmp_path, monkeypatch):
    store = LocalDeliveryStore(tmp_path)
    assert processing_cost.history(store) == [] and processing_cost.history(None) == []
    monkeypatch.setattr(processing_cost, "MAX_RUNS", 3)
    for day in range(5):
        processing_cost.append_run(store, finished_at=f"2026-11-1{day}T06:00:00+00:00", status="complete",
                                   refresh_id=f"run-{day}", totals={"calls": day, "cost_usd": day / 10})
    runs = processing_cost.history(store)
    assert [run["refresh_id"] for run in runs] == ["run-2", "run-3", "run-4"]


def runs(count=4):
    return [{"finished_at": (NOW - timedelta(days=count - 1 - index)).isoformat(), "status": "complete",
             "calls": 40 + index, "calls_without_cost": 0, "cost_usd": 0.1 * (index + 1),
             "prompt_tokens": 100_000 * (index + 1), "completion_tokens": 20_000, "cached_tokens": 0,
             "total_tokens": 100_000 * (index + 1) + 20_000,
             "stages": {"derive": {"calls": 30, "cost_usd": 0.3, "total_tokens": 300_000},
                        "dashboard": {"calls": 2, "cost_usd": 0.1, "total_tokens": 120_000}}}
            for index in range(count)]


def test_page_tops_with_the_last_run_cost_and_a_timeline():
    page = render_page(None, NOW, processing=runs())
    top = page.index("Processing cost")
    assert top < page.index("No report from the board yet")
    assert "$0.400" in page and "420k" in page and "400k in · 20k out" in page and "43" in page
    assert page.index("Facts $0.300") < page.index("Board $0.100")
    assert "Cost per run (USD)" in page and "Tokens per run" in page
    assert page.count("<g><title>") == 8  # a tooltip per run in each of the two charts
    assert "4 runs since" in page and "$1.000 in all" in page


def test_page_says_when_nothing_is_recorded_or_a_cost_is_missing():
    assert "No processing cost recorded yet" in render_page(None, NOW)
    partial = runs(1)
    partial[0]["calls_without_cost"] = 2
    assert "2 calls reported no cost" in render_page(None, NOW, processing=partial)


def test_browser_draws_the_history_from_the_control_store(tmp_path):
    statuses = LocalDeliveryStore(tmp_path / "control")
    statuses.write_json(processing_cost.HISTORY_NAME, {"runs": runs(2)})
    browser = TestClient(browser_app(store=LocalDeliveryStore(tmp_path / "display"),
                                     controller=RefreshController(MemoryStore(), Dispatcher()),
                                     verifier=lambda token, audience: {"email": "viewer@example.com"},
                                     audience="configured", viewers=["viewer@example.com"], owners=[],
                                     origin="https://testserver", status_store=statuses),
                         base_url="https://testserver")
    page = browser.get("/board-status", headers={"X-Goog-IAP-JWT-Assertion": "viewer"})
    assert page.status_code == 200
    assert "Processing cost" in page.text and "$0.200" in page.text
