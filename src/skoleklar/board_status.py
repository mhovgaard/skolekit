"""The board's own status report, kept in the cloud after each sync.

The FireBeetle posts the same document its local page is built from. The
delivery API checks and stores the latest one per device in the control bucket;
the browser service renders it, so the status stays visible while the board
sleeps. Only technical device data is accepted: no school content, no secrets.
"""
from __future__ import annotations

import html
import math
import os
import re
from datetime import UTC, datetime, timedelta

STATUS_PREFIX = "device-status"
MAX_ROWS = 150
MAX_FIELDS = 220
MAX_SAMPLES = 48
STATES = {"ok", "warn", "error", "neutral"}
# A board that should have woken this long ago and has not reported is overdue.
OVERDUE_AFTER = timedelta(minutes=15)
FIELD = re.compile(r"[a-z0-9_]{1,48}")
PLAN_TEXT = {
    "board_changes": "the board changes",
    "scan_published": "a new board is published",
    "retry": "retry after a failed wake",
    "test_interval": "test interval",
    "battery_low": "battery low · Wi-Fi and display skipped",
    "clock_unknown": "retry · the clock is not set",
}


def status_object(device_id: str) -> str:
    return f"{STATUS_PREFIX}/{device_id}.json"


def status_store_from_environment():
    """The control bucket, which both the delivery API and the browser can use."""
    bucket = os.getenv("AG_CONTROL_BUCKET")
    if bucket:
        from skoleklar.delivery_store import GCSDeliveryStore
        return GCSDeliveryStore(bucket)
    root = os.getenv("AG_CONTROL_ROOT")
    if root:
        from pathlib import Path
        from skoleklar.delivery_store import LocalDeliveryStore
        return LocalDeliveryStore(Path(root))
    return None


def _text(value, limit, *, minimum=0):
    if not isinstance(value, str) or not minimum <= len(value) <= limit:
        raise ValueError("Invalid text")
    if any(ord(character) < 32 for character in value):
        raise ValueError("Control character")
    return value


def sanitize_report(value):
    """Keep the readable rows, scalar fields and battery samples; refuse the rest.

    Nested objects other than the battery samples (such as the unit table) are
    dropped rather than stored. Raises ValueError for anything malformed.
    """
    if not isinstance(value, dict):
        raise ValueError("Report must be an object")
    rows = value.get("readable")
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_ROWS:
        raise ValueError("Report needs readable rows")
    readable = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Invalid row")
        state = row.get("state", "neutral")
        if state not in STATES:
            raise ValueError("Invalid row state")
        readable.append({"section": _text(row.get("section"), 40, minimum=1),
                         "label": _text(row.get("label"), 60, minimum=1),
                         "value": _text(row.get("value"), 300), "state": state})
    _text(value.get("firmware"), 40, minimum=1)
    if len(value) > MAX_FIELDS:
        raise ValueError("Too many fields")
    report = {"readable": readable}
    for key, item in value.items():
        if key == "readable" or not FIELD.fullmatch(key):
            continue
        if key == "battery_samples":
            if not isinstance(item, list) or len(item) > MAX_SAMPLES:
                raise ValueError("Invalid battery samples")
            samples = []
            for sample in item:
                if (not isinstance(sample, dict) or not isinstance(sample.get("mv"), int | float)
                        or isinstance(sample.get("mv"), bool) or not 0 <= sample["mv"] <= 6000):
                    raise ValueError("Invalid battery sample")
                samples.append({"at": _text(sample.get("at", ""), 32), "mv": int(sample["mv"])})
            report[key] = samples
        elif item is None or isinstance(item, bool):
            report[key] = item
        elif isinstance(item, int | float):
            if not math.isfinite(item):
                raise ValueError("Invalid number")
            report[key] = item
        elif isinstance(item, str):
            report[key] = _text(item, 200)
    return report


def latest_record(store):
    """The most recently received report across devices, or None."""
    if store is None:
        return None
    newest = None
    for _, record in store.list_json(STATUS_PREFIX):
        if not isinstance(record, dict) or not isinstance(record.get("report"), dict):
            continue
        if newest is None or record.get("received_at", "") > newest.get("received_at", ""):
            newest = record
    return newest


def _zone():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("Europe/Copenhagen")
    except Exception:
        return UTC


def _parse(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else None


def _when(moment, now):
    """Copenhagen clock time, with the date unless it is today."""
    local, today = moment.astimezone(_zone()), now.astimezone(_zone()).date()
    if local.date() == today:
        return local.strftime("%H:%M")
    if local.date() == today + timedelta(days=1):
        return "tomorrow " + local.strftime("%H:%M")
    return local.strftime("%-d %b %H:%M")


def _ago(moment, now):
    seconds = max(0, int((now - moment).total_seconds()))
    if seconds < 60:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    hours = minutes // 60
    if hours < 48:
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    return f"{hours // 24} days ago"


def board_now(report, received, now):
    """What the board is doing now, inferred from its last report: (state, title, detail)."""
    mode = report.get("power_mode")
    remaining = report.get("awake_remaining_ms") or 0
    next_wake = _parse(report.get("next_wake_at"))
    reason = PLAN_TEXT.get(report.get("next_wake_reason"), "longest sleep")
    if mode == "sleep":
        awake_until = received + timedelta(milliseconds=remaining)
        if remaining and awake_until > now:
            return ("ok", "Awake",
                    f"Its own page is reachable on the home network until about {_when(awake_until, now)}.")
        if next_wake and now < next_wake + OVERDUE_AFTER:
            return ("neutral", "Asleep",
                    f"Next wake {_when(next_wake, now)} · {reason}. Press its button to wake it now.")
        if next_wake:
            return ("warn", "No report since the planned wake",
                    f"It planned to wake at {_when(next_wake, now)} and report after syncing.")
        return ("warn", "Asleep", "No next wake was reported.")
    if mode == "awake":
        if next_wake and now >= next_wake + OVERDUE_AFTER:
            return ("warn", "No report since the planned sync",
                    f"It planned to sync at {_when(next_wake, now)} and report afterwards.")
        detail = f"Next automatic sync {_when(next_wake, now)} · {reason}." if next_wake else "Syncs automatically."
        return ("ok", "Always on", detail)
    return ("neutral", "Manual mode", "It reports after each Sync now.")


def _battery(report, now):
    present = report.get("battery_present") is True
    millivolts = report.get("battery_mv") or 0
    if not present:
        return "neutral", "Not detected", "Running on USB power", ""
    level = report.get("battery_percent") or 0
    trend = {"rising": " · rising, probably charging", "falling": " · falling",
             "steady": " · steady"}.get(report.get("battery_trend"), "")
    state = "error" if millivolts < 3450 else "warn" if level < 20 else "ok"
    return state, f"{millivolts / 1000:.2f} V", f"About {int(level)}%{trend}", _sparkline(report, now)


def _sparkline(report, now):
    points = [(moment, sample["mv"]) for sample in report.get("battery_samples") or []
              if (moment := _parse(sample.get("at"))) and sample["mv"] >= 2500]
    if len(points) < 2:
        return ""
    points.sort()
    first, last = points[0][0], points[-1][0]
    span = max((last - first).total_seconds(), 1)
    low, high = min(mv for _, mv in points), max(mv for _, mv in points)
    middle, half = (low + high) / 2, max((high - low) / 2, 50) + 10
    width, height = 280, 56

    def xy(moment, mv):
        x = 4 + (width - 8) * (moment - first).total_seconds() / span
        y = 4 + (height - 8) * (1 - (mv - (middle - half)) / (2 * half))
        return f"{x:.1f},{y:.1f}"

    line = " ".join(xy(moment, mv) for moment, mv in points)
    tip = xy(*points[-1]).split(",")
    return (f"<svg class=spark viewBox='0 0 {width} {height}' role=img "
            f"aria-label='Battery voltage, {len(points)} readings from {low / 1000:.2f} to {high / 1000:.2f} V'>"
            f"<polyline points='{line}' fill=none stroke=currentColor stroke-width=2 "
            "stroke-linejoin=round stroke-linecap=round />"
            f"<circle cx='{tip[0]}' cy='{tip[1]}' r=3.5 fill=currentColor /></svg>"
            f"<small>{len(points)} readings · {_when(first, now)} to {_when(last, now)} · "
            f"{low / 1000:.2f}–{high / 1000:.2f} V</small>")


BOARD_CSS = """
*{box-sizing:border-box}body{font:15px system-ui,-apple-system,sans-serif;line-height:1.45;max-width:760px;
 margin:0 auto;padding:28px 20px;color:#25352d;background:#f6f8f6}
nav{font-size:14px;margin-bottom:18px}nav a{color:#216a40;text-underline-offset:3px}
h1{font-size:30px;margin:4px 0 6px}h2{font-size:13px;text-transform:uppercase;letter-spacing:.08em;color:#67756c;
 margin:24px 0 8px}p{margin:8px 0}small,.intro{color:#67756c}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px;margin:18px 0 6px}
.card{background:white;border:1px solid #dfe6e0;border-radius:6px;padding:14px 16px}
.card h2{margin:0 0 6px}.card .big{font-size:26px;font-weight:650;font-variant-numeric:tabular-nums;line-height:1.2}
.card p{margin:4px 0 0}.card small{display:block;margin-top:4px}
.spark{display:block;width:100%;height:56px;margin-top:10px}
.card.state-ok .big,.card.state-ok .spark{color:#1c7040}.card.state-warn .big,.card.state-warn .spark{color:#895800}
.card.state-error .big,.card.state-error .spark{color:#aa3030}.card.state-neutral .spark{color:#67756c}
table{border-collapse:collapse;width:100%;background:white;border:1px solid #dfe6e0}
td,th{text-align:left;padding:11px 12px;border-bottom:1px solid #e8ede8;overflow-wrap:anywhere;vertical-align:top}
th{width:38%;font-weight:500}td{font-variant-numeric:tabular-nums}.checks{counter-reset:step}
.checks th:before{counter-increment:step;content:counter(step,decimal-leading-zero);color:#9aa59e;font-size:12px;
 margin-right:12px}.signal{display:inline-block;font-size:10px;font-weight:750;letter-spacing:.03em;margin-right:8px;
 min-width:30px;color:#67756c}.state-ok .signal,tr.state-ok td{color:#1c7040}
.state-warn .signal,tr.state-warn td{color:#895800}.state-error .signal,tr.state-error td{color:#aa3030}
.state-neutral .signal{display:none}details{margin-top:24px}summary{cursor:pointer;font-weight:600}
details table{margin:12px 0}footer{font-size:12px;color:#67756c;margin-top:24px}
.empty{background:white;border:1px solid #dfe6e0;border-radius:6px;padding:18px 20px;margin-top:18px}
@media(max-width:480px){body{padding:20px 12px}td,th{padding:10px 9px}th{width:40%}
 .checks th:before{display:block;margin:0 0 2px}.signal{display:block;margin-bottom:2px}h1{font-size:27px}}
"""
BADGES = {"ok": "OK", "warn": "WAIT", "error": "FAIL", "neutral": "—"}


def _page(body):
    return ("<!doctype html><html lang=en><head><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            "<meta http-equiv=refresh content=60><title>Board status</title>"
            f"<style>{BOARD_CSS}</style></head><body>"
            "<nav aria-label='Visning'><a href='/'>← Skoleoverblik</a></nav>"
            "<p class=intro>AG board · FireBeetle</p>" + body + "</body></html>")


def render_page(record, now=None):
    """The cloud copy of the board's status page, with what it is doing now."""
    now = now or datetime.now(UTC)
    escape = html.escape
    if record is None:
        return _page("<h1>Board status</h1><div class=empty><p><strong>No report from the board yet.</strong></p>"
                     "<p>From firmware ag-cloud-0.7.0 the board sends its status here after every sync. "
                     "Press its button to wake it; a report follows within a minute.</p></div>")
    report = record["report"]
    received = _parse(record.get("received_at")) or now
    state, title, detail = board_now(report, received, now)
    battery_state, voltage, level, spark = _battery(report, now)
    body = ["<h1>Board status</h1>",
            f"<p><small>Reported {escape(_when(received, now))} · {escape(_ago(received, now))} · "
            f"{escape(str(report.get('firmware', '')))}. This page reloads every minute.</small></p>",
            "<div class=cards>",
            f"<section class='card state-{battery_state}'><h2>Battery</h2>"
            f"<div class=big>{escape(voltage)}</div><p>{escape(level)}</p>{spark}</section>",
            f"<section class='card state-{state}'><h2>Board now</h2><div class=big>{escape(title)}</div>"
            f"<p>{escape(detail)}</p></section></div>"]
    previous = None
    for row in report["readable"]:
        section = row["section"]
        if section != previous:
            if previous is not None:
                body.append("</table>")
            if section == "Technical details":
                body.append("<details><summary>Technical details</summary>")
            else:
                heading = f"{section} at {_when(received, now)}" if section == "Status" else section
                body.append(f"<h2>{escape(heading)}</h2>")
            body.append("<table class=checks>" if section == "Status" else "<table>")
            previous = section
        row_state = row["state"]
        body.append(f"<tr class='state-{row_state}'><th scope=row>{escape(row['label'])}</th>"
                    f"<td><span class=signal>{BADGES[row_state]}</span>{escape(row['value'])}</td></tr>")
    body.append("</table>")
    if previous == "Technical details":
        body.append("</details>")
    body.append("<footer>Times: Copenhagen (CET/CEST). kB = 1,000 bytes. The rows are the board's own status "
                "page as it was when it reported; it reports after every sync, so while it sleeps this is its "
                "last known state. Sync now and the other controls stay on the board's own page.</footer>")
    return _page("".join(body))
