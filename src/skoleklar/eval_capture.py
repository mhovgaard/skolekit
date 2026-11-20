"""Every model call a scan makes, kept as eval cases, when the job is told to.

`SKOLEKLAR_EVAL_CAPTURE=1` turns it on. A scan then keeps, per model call, what
`OpenRouterClient.generate` sent and got back: the operation (manifest ID and
hash), the scan stage, the model, the input document, the output, the usage and
how long it took. Each distinct rendered system prompt is kept once per run. At
the end the scan adds what its code made of the outputs (`note`): the facts that
reached the phone, each board week's composition, the run's outputs and health.

The run's records go to the private state, beside the board's composition files,
as one JSON Lines file: `dashboard/evals/YYYY-MM-DD/RUN_ID.jsonl` in the cloud's
private bucket, where a lifecycle rule deletes them after 30 days. They hold school
text and the household's names, as the database does, and never leave that bucket.

Message operations (`message-*` manifests) are never kept: they read message
bodies, which the board promises are not retained (docs/message-harvesting.md).

Capture never changes a scan: a call is recorded after it returns or fails, and
an error while recording or writing is logged by name only and otherwise ignored.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import UTC, datetime
from typing import Any

ENV = "SKOLEKLAR_EVAL_CAPTURE"
FOLDER = "evals"
FORMAT = 1
# Operations that read message bodies: never kept.
EXCLUDED_PREFIXES = ("message-",)

_lock = threading.Lock()
_run: dict[str, Any] | None = None


def enabled() -> bool:
    return os.environ.get(ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def start(run_id: str, *, model: str | None = None, at: datetime | None = None) -> None:
    """Begin a run's capture, when it is turned on; otherwise forget any earlier one.

    `at` is when the run started (UTC); its date names the run's folder."""
    global _run
    with _lock:
        _run = None
        if enabled():
            _run = {"run_id": str(run_id), "started_at": at or datetime.now(UTC), "model": model,
                    "prompts": {}, "calls": [], "notes": {}}


def active() -> bool:
    return _run is not None


def record(manifest: Any, *, model: str, document: Any, max_output_tokens: int, stage: str,
           seconds: float, response: dict[str, Any] | None = None,
           error: BaseException | None = None) -> None:
    """Keep one model call of the current run; nothing when capture is off or the operation is excluded."""
    run = _run
    if run is None:
        return
    manifest_id = str(getattr(manifest, "manifest_id", ""))
    if manifest_id.startswith(EXCLUDED_PREFIXES):
        return
    prompt = manifest.render_instructions()
    prompt_sha = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    call: dict[str, Any] = {
        "kind": "call",
        "at": datetime.now(UTC).isoformat(),
        "stage": stage,
        "manifest_id": manifest_id,
        "manifest_hash": getattr(manifest, "content_hash", None),
        "prompt_sha256": prompt_sha,
        "model": model,
        "max_output_tokens": max_output_tokens,
        "seconds": round(seconds, 3),
        "input": document,
    }
    if response is not None:
        call.update(output=response.get("output"), finish_reason=response.get("finish_reason"),
                    response_id=response.get("response_id"), usage=response.get("usage"))
    if error is not None:
        call["error"] = {"type": type(error).__name__,
                         **{field: getattr(error, field) for field in ("error_code", "stage", "status_code",
                                                                      "finish_reason", "usage")
                            if getattr(error, field, None) is not None}}
    with _lock:
        if _run is not run:
            return
        run["prompts"].setdefault(prompt_sha, {"kind": "prompt", "sha256": prompt_sha,
                                               "manifest_id": manifest_id, "text": prompt})
        call["seq"] = len(run["calls"]) + 1
        run["calls"].append(call)


def note(name: str, value: Any) -> None:
    """Add what the scan's code made of the outputs under `name` (the run's last line)."""
    with _lock:
        if _run is not None:
            _run["notes"][name] = value


def object_name() -> str | None:
    """Where the current run's file goes in the private state, or None without a capture."""
    run = _run
    if run is None:
        return None
    return f"{FOLDER}/{run['started_at'].date().isoformat()}/{run['run_id']}.jsonl"


def dump() -> str | None:
    """The current run as JSON Lines: the run, each prompt once, each call, then the outcome."""
    with _lock:
        run = _run
        if run is None:
            return None
        lines = [{"kind": "run", "format": FORMAT, "run_id": run["run_id"],
                  "started_at": run["started_at"].isoformat(), "model": run["model"],
                  "call_count": len(run["calls"])},
                 *run["prompts"].values(), *run["calls"],
                 {"kind": "outcome", **run["notes"]}]
    return "".join(json.dumps(line, ensure_ascii=False, default=str, separators=(",", ":")) + "\n"
                   for line in lines)


def finish() -> None:
    global _run
    with _lock:
        _run = None
