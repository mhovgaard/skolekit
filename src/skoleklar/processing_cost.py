"""What each scan spent on the model, in US dollars and tokens.

Every model call goes through `OpenRouterClient.generate`, which reports the
call's `usage` here (`record`). A scan resets the meter when it starts, names
the stage it is in (`stage`), and when it ends appends the run's totals to a
short history in the control bucket (`append_run`), beside latest-run.json.
The browser's Tavlestatus page draws that history (`render_section`).

Only counts and money are kept: no prompts, no output, no school content.
OpenRouter reports `usage.cost` in US dollars; a call without it is counted
in `calls_without_cost`, so a total is never shown as complete when it is not.
"""
from __future__ import annotations

import contextlib
import html
import math
import threading
from datetime import UTC, datetime, timedelta
from typing import Any

HISTORY_NAME = "processing-cost.json"
MAX_RUNS = 120
TOKEN_FIELDS = ("prompt_tokens", "completion_tokens", "total_tokens", "cached_tokens", "reasoning_tokens")

_lock = threading.Lock()
_totals: dict[str, Any] = {}
_stage = "other"


def _empty() -> dict[str, Any]:
    return {"calls": 0, "calls_without_cost": 0, "cost_usd": 0.0, **{field: 0 for field in TOKEN_FIELDS}}


def reset() -> None:
    """Start a new run's meter."""
    global _totals, _stage
    with _lock:
        _totals = {**_empty(), "stages": {}}
        _stage = "other"


def set_stage(name: str) -> None:
    """Attribute the calls from here on to `name`."""
    global _stage
    _stage = name


def current_stage() -> str:
    """The stage the calls are attributed to now."""
    return _stage


@contextlib.contextmanager
def stage(name: str):
    """Attribute the calls made inside to `name`."""
    global _stage
    previous, _stage = _stage, name
    try:
        yield
    finally:
        _stage = previous


def _count(value: Any) -> int:
    return int(value) if isinstance(value, int | float) and not isinstance(value, bool) and value > 0 else 0


def record(usage: Any) -> None:
    """Add one model call's OpenRouter `usage` to the current run."""
    usage = usage if isinstance(usage, dict) else {}
    prompt_details = usage.get("prompt_tokens_details") or {}
    completion_details = usage.get("completion_tokens_details") or {}
    counts = {
        "prompt_tokens": _count(usage.get("prompt_tokens")),
        "completion_tokens": _count(usage.get("completion_tokens")),
        "total_tokens": _count(usage.get("total_tokens")),
        "cached_tokens": _count(prompt_details.get("cached_tokens") if isinstance(prompt_details, dict) else 0),
        "reasoning_tokens": _count(completion_details.get("reasoning_tokens")
                                   if isinstance(completion_details, dict) else 0),
    }
    if not counts["total_tokens"]:
        counts["total_tokens"] = counts["prompt_tokens"] + counts["completion_tokens"]
    cost = usage.get("cost")
    known = isinstance(cost, int | float) and not isinstance(cost, bool) and math.isfinite(cost) and cost >= 0
    with _lock:
        if not _totals:
            _totals.update({**_empty(), "stages": {}})
        stage_totals = _totals["stages"].setdefault(_stage, _empty())
        for bucket in (_totals, stage_totals):
            bucket["calls"] += 1
            for field, value in counts.items():
                bucket[field] += value
            if known:
                bucket["cost_usd"] += float(cost)
            else:
                bucket["calls_without_cost"] += 1


def snapshot() -> dict[str, Any]:
    """The current run's totals, with costs rounded to a millionth of a dollar."""
    with _lock:
        totals = {**_empty(), "stages": {}, **_totals}
        stages = {name: dict(value) for name, value in totals["stages"].items()}
    totals = {**totals, "stages": stages}
    for bucket in (totals, *stages.values()):
        bucket["cost_usd"] = round(bucket["cost_usd"], 6)
    return totals


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


def append_run(store, *, finished_at: str, status: str, refresh_id: str | None, totals: dict[str, Any]) -> None:
    """Add one run to the history, keeping the newest MAX_RUNS."""
    if store is None:
        return
    entry = {"finished_at": finished_at, "status": status, **({"refresh_id": refresh_id} if refresh_id else {}),
             **totals}
    runs = [*history(store), entry][-MAX_RUNS:]
    store.write_json(HISTORY_NAME, {"runs": runs})


# The page -------------------------------------------------------------------

STAGE_TEXT = {
    "harvest": "Messages", "derive": "Facts", "archive_plan": "Archive plan", "archive": "Archive",
    "consolidate": "Consolidate", "day_summaries": "Day summaries", "review_checks": "Review checks",
    "dashboard": "Board", "other": "Other",
}

CSS = """
.cost{background:white;border:1px solid #dfe6e0;border-radius:6px;padding:14px 16px;margin:18px 0 6px}
.cost h2{margin:0 0 10px}.cost-stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.cost .big{font-size:26px;font-weight:650;font-variant-numeric:tabular-nums;line-height:1.2}
.cost-stats p{margin:2px 0 0;color:#67756c;font-size:13px}
.cost-chart{margin-top:14px}.cost-chart h3{font-size:12px;font-weight:600;color:#67756c;margin:10px 0 2px}
.cost-chart svg{display:block;width:100%;height:auto;color:#2f7a52;overflow:visible}
.cost-chart text{font:11px system-ui,-apple-system,sans-serif;fill:#67756c;font-variant-numeric:tabular-nums}
.cost-chart .grid{stroke:#e8ede8;stroke-width:1}.cost-chart .axis{stroke:#cfd8d1;stroke-width:1}
.cost-chart .hit{fill:transparent}.cost-chart .hit:hover{fill:rgba(47,122,82,.08)}
.cost-chart .bar{fill:currentColor}.cost-chart .bar.last{fill:#1c5a3a}
.cost-stages{margin-top:10px;font-size:13px;color:#67756c}.cost-stages span{white-space:nowrap;margin-right:12px}
.cost small{display:block;margin-top:8px}
"""


def money(value: float) -> str:
    if value >= 10:
        return f"${value:,.2f}"
    if value >= 0.01:
        return f"${value:.3f}"
    return f"${value:.4f}"


def tokens(value: int | float) -> str:
    value = int(value)
    if value >= 1_000_000:
        return f"{value / 1_000_000:.2f}M"
    if value >= 10_000:
        return f"{value / 1000:.0f}k"
    if value >= 1000:
        return f"{value / 1000:.1f}k"
    return str(value)


def _parse(value):
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _nice_ceiling(value: float) -> float:
    if value <= 0:
        return 1.0
    exponent = 10 ** math.floor(math.log10(value))
    for step in (1, 2, 2.5, 5, 10):
        if value <= step * exponent:
            return step * exponent
    return 10 * exponent


def _bars(points, key, label, fmt, first, span, zone, *, dates=False, width=440, height=84):
    """One small bar chart on a shared time axis: one bar per run, a native tooltip per bar."""
    left, right, top, bottom = 44, 6, 7, 16
    plot_w, plot_h = width - left - right, height - top - bottom
    ceiling = _nice_ceiling(max(point[key] for point in points))
    bar = max(3.0, min(14.0, plot_w / max(len(points), 1) * 0.6))
    hit = max(bar + 6, min(28.0, plot_w / max(len(points), 1)))
    parts = [f"<svg viewBox='0 0 {width} {height}' role=img aria-label='{html.escape(label, quote=True)}, "
             f"{len(points)} runs'>"]
    for fraction in (0.5, 1.0):
        y = top + plot_h * (1 - fraction)
        parts.append(f"<line class=grid x1={left} x2={width - right} y1={y:.1f} y2={y:.1f} />"
                     f"<text x={left - 6} y={y + 4:.1f} text-anchor=end>{html.escape(fmt(ceiling * fraction))}</text>")
    parts.append(f"<line class=axis x1={left} x2={width - right} y1={top + plot_h} y2={top + plot_h} />")
    if dates and span:
        last_at = first + timedelta(seconds=span)
        for at, x, anchor in ((first, left, "start"), (last_at, width - right, "end")):
            parts.append(f"<text x={x} y={height - 3} text-anchor={anchor}>"
                         f"{html.escape(at.astimezone(zone).strftime('%-d %b'))}</text>")
    for index, point in enumerate(points):
        x = left + plot_w * ((point["at"] - first).total_seconds() / span if span else 0.5)
        value_h = plot_h * point[key] / ceiling
        y = top + plot_h - value_h
        local = point["at"].astimezone(zone).strftime("%-d %b %H:%M")
        tip = (f"{local} · {money(point['cost_usd'])} · {tokens(point['total_tokens'])} tokens "
               f"({tokens(point['prompt_tokens'])} in, {tokens(point['completion_tokens'])} out) · "
               f"{point['calls']} calls")
        last = " last" if index == len(points) - 1 else ""
        # Rounded top only: a 2px-radius rect, with its bottom corners squared by a short rect.
        parts.append(f"<g><title>{html.escape(tip)}</title>"
                     f"<rect class=hit x={x - hit / 2:.1f} y={top} width={hit:.1f} height={plot_h} />")
        if value_h > 0:
            parts.append(f"<rect class='bar{last}' x={x - bar / 2:.1f} y={y:.1f} width={bar:.1f} "
                         f"height={value_h:.1f} rx=2 />")
            if value_h > 2:
                parts.append(f"<rect class='bar{last}' x={x - bar / 2:.1f} y={top + plot_h - min(2, value_h):.1f} "
                             f"width={bar:.1f} height={min(2, value_h):.1f} />")
        parts.append("</g>")
    parts.append("</svg>")
    return f"<h3>{html.escape(label)}</h3>" + "".join(parts)


def render_section(runs, now=None, zone=UTC, days=30) -> str:
    """The processing block for the top of Tavlestatus: the last run, then cost and tokens per run."""
    now = now or datetime.now(UTC)
    if not runs:
        return ("<section class=cost><h2>Processing cost</h2><p>No processing cost recorded yet. "
                "Each scan records what it spent on the model when it finishes.</p></section>")
    last = runs[-1]
    escape = html.escape
    finished = _parse(last.get("finished_at"))
    when = finished.astimezone(zone).strftime("%-d %b %H:%M") if finished else "unknown time"
    cost, total = float(last.get("cost_usd") or 0), int(last.get("total_tokens") or 0)
    missing = int(last.get("calls_without_cost") or 0)
    cached = int(last.get("cached_tokens") or 0)
    stats = [
        f"<div><div class=big>{escape(money(cost))}</div><p>Last processing · {escape(when)}"
        f"{' · ' + escape(str(last.get('status'))) if last.get('status') not in (None, 'complete') else ''}</p></div>",
        f"<div><div class=big>{escape(tokens(total))}</div><p>tokens · {escape(tokens(last.get('prompt_tokens') or 0))}"
        f" in · {escape(tokens(last.get('completion_tokens') or 0))} out"
        f"{' · ' + escape(tokens(cached)) + ' cached' if cached else ''}</p></div>",
        f"<div><div class=big>{int(last.get('calls') or 0)}</div><p>model calls</p></div>",
    ]
    since = now - timedelta(days=days)
    points = []
    for run in runs:
        moment = _parse(run.get("finished_at"))
        if moment and moment >= since:
            points.append({"at": moment, "cost_usd": float(run.get("cost_usd") or 0),
                           "total_tokens": int(run.get("total_tokens") or 0),
                           "prompt_tokens": int(run.get("prompt_tokens") or 0),
                           "completion_tokens": int(run.get("completion_tokens") or 0),
                           "calls": int(run.get("calls") or 0)})
    chart = ""
    if points:
        first = min(point["at"] for point in points)
        span = (max(point["at"] for point in points) - first).total_seconds()
        period = sum(point["cost_usd"] for point in points)
        chart = ("<div class=cost-chart>"
                 + _bars(points, "cost_usd", "Cost per run (USD)", lambda value: f"${value:g}", first, span, zone)
                 + _bars(points, "total_tokens", "Tokens per run", tokens, first, span, zone, dates=True)
                 + f"<small>{len(points)} run{'s' if len(points) != 1 else ''} since "
                 f"{escape(first.astimezone(zone).strftime('%-d %b'))} · {escape(money(period))} in all · "
                 "hover a bar for the run.</small></div>")
    stages = sorted(((name, value) for name, value in (last.get("stages") or {}).items()
                     if isinstance(value, dict) and value.get("calls")),
                    key=lambda item: -float(item[1].get("cost_usd") or 0))
    stage_line = ("<div class=cost-stages>" + " ".join(
        f"<span>{escape(STAGE_TEXT.get(name, name))} {escape(money(float(value.get('cost_usd') or 0)))} · "
        f"{escape(tokens(value.get('total_tokens') or 0))}</span>" for name, value in stages) + "</div>"
                  if stages else "")
    note = (f"<small>{missing} call{'s' if missing != 1 else ''} reported no cost; "
            "the dollar figure is a lower bound.</small>" if missing else "")
    return ("<section class=cost><h2>Processing cost</h2><div class=cost-stats>" + "".join(stats) + "</div>"
            + stage_line + note + chart + "</section>")
