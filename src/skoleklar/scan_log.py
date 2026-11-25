"""Each scan's own log, kept for Tavlestatus.

Every scan event (`skoleklar.runtime.scan._event`) already goes to the job's log
with counts, stage names and fixed codes only. The scan also notes them here
(`note`), and when it ends appends the run, its outcome and those events to a
short history in the control bucket (`append_run`), beside latest-run.json and
the cost history. Tavlestatus draws it as a folding list (`render_section`), so
a failing scan explains itself without opening Cloud Logging.

Only scalar fields are kept, and strings are cut short: no school content, no
secrets, nothing that is not already in the job's log.
"""
from __future__ import annotations

import html
import re
import threading
from datetime import UTC, datetime
from typing import Any

HISTORY_NAME = "scan-log.json"
MAX_RUNS = 30
MAX_EVENTS = 80
MAX_TEXT = 120
_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}")

_lock = threading.Lock()
_events: list[dict[str, Any]] = []

# What a failure code means, for a parent reading the page.
REASONS = {
    "provider_http_401": "OpenRouter rejected the API key (expired or revoked). Rotate the "
                         "agskoleintra-openrouter-api-key secret.",
    "provider_http_402": "OpenRouter is out of credit. Top up the account or raise the key's limit.",
    "provider_http_403": "OpenRouter refused the key for this model or limit.",
    "provider_http_429": "OpenRouter rate-limited the scan. The next scan usually recovers.",
    "provider_timeout": "The model did not answer in time. The next scan usually recovers.",
    "provider_key_missing": "No OpenRouter API key is configured for the scan.",
    "school_login_failed": "The automatic school login did not produce a session. Check the "
                           "school username and password secrets, or sign in once with `skoleklar login`.",
    "school_session_expired": "The saved school session has expired and automatic login is off.",
    "source_incomplete": "A school page could not be read completely.",
    "profile_missing": "The household's school profile is missing or broken.",
    "consolidation_validation": "Consolidating the facts failed its checks.",
    "invalid_provider_json": "The model answered with something that was not valid JSON.",
    "command_failed": "The stage failed without a known reason.",
}
STAGE_TEXT = {
    "harvest": "School pages and messages", "derive": "Facts", "archive_plan": "Archive plan",
    "archive": "Archive", "consolidate": "Consolidate", "manifest": "Phone manifest",
    "dashboard": "Board", "scan": "Scan",
}


def reset() -> None:
    """Start a new run's log."""
    with _lock:
        _events.clear()


def _scalar(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return value[:MAX_TEXT]
    return None


def note(name: str, fields: dict[str, Any], *, at: datetime | None = None) -> None:
    """Add one event; nested values (the run summary's outputs, the costs) are left out."""
    entry = {"at": (at or datetime.now(UTC)).isoformat(timespec="seconds"), "event": str(name)[:48]}
    for key, value in fields.items():
        kept = _scalar(value)
        if kept is not None or value is None:
            entry[str(key)[:48]] = kept
    with _lock:
        if len(_events) < MAX_EVENTS:
            _events.append(entry)


def events() -> list[dict[str, Any]]:
    with _lock:
        return [dict(entry) for entry in _events]


def _code(value: Any) -> str | None:
    return value if isinstance(value, str) and _CODE.fullmatch(value) else None


def failure(summary: dict[str, Any], logged: list[dict[str, Any]]) -> dict[str, str] | None:
    """(stage, code) of what stopped the board from getting a new frame, or None."""
    for entry in reversed(logged):
        if entry["event"] == "stage_failed":
            return {"stage": _code(entry.get("stage")) or "scan",
                    "code": _code(entry.get("failure_code")) or "command_failed"}
    dashboard = (summary.get("outputs") or {}).get("dashboard") or {}
    if dashboard.get("status") == "failed":
        code = next((_code(entry.get("failure_code")) for entry in reversed(logged)
                     if entry["event"] == "dashboard_failed"), None)
        return {"stage": "dashboard", "code": code or _code(_snake(dashboard.get("error_type"))) or "command_failed"}
    if summary.get("status") == "failed":
        return {"stage": "scan", "code": _code(_snake(summary.get("error_type"))) or "command_failed"}
    return None


def _snake(name: Any) -> str | None:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower() if isinstance(name, str) else None


def is_critical(summary: dict[str, Any]) -> bool:
    """The board got no new frame: the scan failed, or its board output did."""
    dashboard = (summary.get("outputs") or {}).get("dashboard") or {}
    return summary.get("status") == "failed" or dashboard.get("status") == "failed"


def history(store) -> list[dict[str, Any]]:
    """The stored runs, oldest first; empty when there is no store or no history yet."""
    if store is None:
        return []
    try:
        document = store.read_json(HISTORY_NAME)
    except Exception:
        return []
    runs = document.get("runs") if isinstance(document, dict) else None
    return [run for run in runs if isinstance(run, dict) and isinstance(run.get("finished_at"), str)] \
        if isinstance(runs, list) else []


def append_run(store, summary: dict[str, Any]) -> dict[str, Any] | None:
    """Add this run, its outcome and its events to the history, keeping the newest MAX_RUNS."""
    if store is None:
        return None
    logged = events()
    entry = {"finished_at": summary.get("finished_at") or datetime.now(UTC).isoformat(),
             "status": summary.get("status", "failed"),
             **({"refresh_id": summary["refresh_id"]} if summary.get("refresh_id") else {}),
             "critical": is_critical(summary), "events": logged}
    found = failure(summary, logged)
    if found:
        entry["failure"] = found
    runs = [*history(store), entry][-MAX_RUNS:]
    store.write_json(HISTORY_NAME, {"runs": runs})
    return entry


# The page -------------------------------------------------------------------

CSS = """
.scanlog summary{display:flex;flex-wrap:wrap;gap:4px 10px;align-items:baseline}
.scanlog summary .what{font-weight:600}.scanlog summary small{font-weight:400}
.scanlog .reason{margin:10px 0 4px}.scanlog details{margin:0}.scanlog details summary{font-weight:500}
.scanlog .runs td,.scanlog .runs th{padding:9px 10px}.scanlog .events{margin:8px 0 4px;font-size:13px}
.scanlog .events td{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px}
.scanlog .events th{width:auto;white-space:nowrap}.scanlog .alert{color:#aa3030;font-weight:700}
"""


def _parse(value: Any) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else None


def _clock(moment: datetime, now: datetime, zone) -> str:
    local = moment.astimezone(zone)
    return local.strftime("%H:%M") if local.date() == now.astimezone(zone).date() \
        else local.strftime("%-d %b %H:%M")


def reason_text(found: dict[str, str] | None) -> str:
    if not found:
        return ""
    stage = STAGE_TEXT.get(found["stage"], found["stage"])
    return f"{stage}: {REASONS.get(found['code'], found['code'])}"


def _fields(entry: dict[str, Any]) -> str:
    return " · ".join(f"{key}={value}" for key, value in entry.items() if key not in {"at", "event"})


def render_section(runs: list[dict[str, Any]], now: datetime, zone) -> str:
    """A folding 'Scan log': the newest run's outcome in the summary, every run's events inside."""
    escape = html.escape
    if not runs:
        return ("<details class=scanlog><summary><span class=what>Scan log</span>"
                "<small>No scans recorded yet</small></summary></details>")
    newest = runs[-1]
    when = _parse(newest["finished_at"])
    stamp = _clock(when, now, zone) if when else ""
    failed_in_row = 0
    for run in reversed(runs):
        if not run.get("critical"):
            break
        failed_in_row += 1
    if newest.get("critical"):
        headline = (f"<span class=alert>⚠ Last {failed_in_row} scans failed</span>" if failed_in_row > 1
                    else "<span class=alert>⚠ Last scan failed</span>")
        state = "error"
    elif newest.get("status") == "partial":
        headline, state = "Last scan partial", "warn"
    else:
        headline, state = "Last scan OK", "ok"
    parts = [f"<details class='scanlog state-{state}'><summary><span class=what>Scan log</span>"
             f"<span>{headline}</span><small>{escape(stamp)}</small></summary>"]
    if newest.get("failure"):
        parts.append(f"<p class=reason>{escape(reason_text(newest['failure']))}</p>")
    parts.append("<table class=runs>")
    for run in reversed(runs):
        moment = _parse(run["finished_at"])
        status = run.get("status", "")
        row_state = "error" if run.get("critical") else "warn" if status == "partial" else "ok"
        label = {"error": "FAIL", "warn": "PART", "ok": "OK"}[row_state]
        detail = reason_text(run.get("failure")) or ("Board published" if row_state == "ok" else status)
        events_html = "".join(
            f"<tr><th scope=row>{escape(_clock(_parse(entry['at']) or now, now, zone))} "
            f"{escape(entry['event'])}</th><td>{escape(_fields(entry))}</td></tr>"
            for entry in run.get("events") or [])
        parts.append(
            f"<tr class='state-{row_state}'><th scope=row>{escape(_clock(moment, now, zone) if moment else '')}</th>"
            f"<td><span class=signal>{label}</span>{escape(detail)}"
            + (f"<details><summary>{len(run.get('events') or [])} events</summary>"
               f"<table class=events>{events_html}</table></details>" if events_html else "")
            + "</td></tr>")
    parts.append("</table><small>Newest first · the last "
                 f"{MAX_RUNS} scans · the same safe events as the job's own log.</small></details>")
    return "".join(parts)
