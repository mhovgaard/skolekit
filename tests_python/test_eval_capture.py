"""Eval capture: each model call kept as a case, message operations never. Synthetic content only."""
import json

import httpx
import pytest

from skoleklar import eval_capture, processing_cost
from skoleklar.triage.derivation import OpenRouterClient, OpenRouterGenerationError
from skoleklar.triage.prompt_manifest import load_prompt_manifest


def client_answering(*answers):
    replies = iter(answers)
    return OpenRouterClient(api_key="test", model="synthetic/model-1", client=httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=next(replies)))))


def answer(content, **usage):
    return {"id": "r1", "usage": usage, "choices": [{"finish_reason": "stop", "message": {"content": content}}]}


@pytest.fixture(autouse=True)
def fresh():
    eval_capture.finish()
    processing_cost.reset()
    yield
    eval_capture.finish()


def lines():
    return [json.loads(line) for line in eval_capture.dump().splitlines()]


def test_each_call_is_kept_with_its_input_output_usage_and_stage(monkeypatch):
    monkeypatch.setenv(eval_capture.ENV, "1")
    eval_capture.start("run-1", model="synthetic/model-1")
    client = client_answering(answer(json.dumps({"verdicts": []}), prompt_tokens=10, cost=0.001),
                              answer("not json", prompt_tokens=5))
    manifest = load_prompt_manifest("review-check-v1")
    with processing_cost.stage("review_checks"):
        client.generate({"facts": ["Alfa: idrætsdag"]}, manifest=manifest, max_output_tokens=900)
        with pytest.raises(OpenRouterGenerationError):
            client.generate({"facts": []}, manifest=manifest)
    eval_capture.note("summary", {"status": "complete"})

    run, prompt, first, second, outcome = lines()
    assert run["kind"] == "run" and run["run_id"] == "run-1" and run["call_count"] == 2
    # The rendered prompt is kept once, and each call names it by hash.
    assert prompt["kind"] == "prompt" and prompt["manifest_id"] == "review-check-v1"
    assert prompt["text"] == manifest.render_instructions()
    assert first["prompt_sha256"] == second["prompt_sha256"] == prompt["sha256"]
    assert first["manifest_hash"] == manifest.content_hash
    assert (first["seq"], first["stage"], first["model"], first["max_output_tokens"]) == (
        1, "review_checks", "synthetic/model-1", 900)
    assert first["input"] == {"facts": ["Alfa: idrætsdag"]}
    assert first["output"] == {"verdicts": []} and first["usage"]["cost"] == 0.001
    assert "error" not in first
    assert second["error"]["error_code"] == "model_output_not_json" and "output" not in second
    assert outcome == {"kind": "outcome", "summary": {"status": "complete"}}
    assert eval_capture.object_name().endswith("/run-1.jsonl")


def test_message_operations_are_never_kept(monkeypatch):
    monkeypatch.setenv(eval_capture.ENV, "1")
    eval_capture.start("run-2")
    client = client_answering(answer(json.dumps({"events": []})))
    client.generate({"body": "a message body"}, manifest=load_prompt_manifest("message-events-v4"))
    assert [line["kind"] for line in lines()] == ["run", "outcome"]
    assert "a message body" not in eval_capture.dump()


def test_off_by_default_keeps_nothing(monkeypatch):
    monkeypatch.delenv(eval_capture.ENV, raising=False)
    eval_capture.start("run-3")
    client_answering(answer(json.dumps({"verdicts": []}))).generate(
        {}, manifest=load_prompt_manifest("review-check-v1"))
    assert not eval_capture.active() and eval_capture.dump() is None and eval_capture.object_name() is None


def test_a_broken_capture_never_breaks_the_call(monkeypatch):
    monkeypatch.setenv(eval_capture.ENV, "1")
    eval_capture.start("run-4")
    monkeypatch.setattr(eval_capture, "record", lambda *a, **k: 1 / 0)
    result = client_answering(answer(json.dumps({"verdicts": []}))).generate(
        {}, manifest=load_prompt_manifest("review-check-v1"))
    assert result["output"] == {"verdicts": []}
