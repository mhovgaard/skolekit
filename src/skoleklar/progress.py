"""Progress lines on stderr for the long commands; stdout stays the result."""
from __future__ import annotations

import sys
from typing import Any


def _progress_enabled(args: Any) -> bool:
    mode = getattr(args, "progress", "auto")
    return mode == "plain" or (mode == "auto" and sys.stderr.isatty())


def _emit_progress(
    args: Any,
    label: str,
    *,
    detail: str = "",
    done: bool = False,
) -> None:
    if not _progress_enabled(args):
        return
    marker = "✓" if done else "…"
    suffix = f" · {detail}" if detail else ""
    print(f"{marker} {label}{suffix}", file=sys.stderr, flush=True)


def _elapsed_text(milliseconds: int | float) -> str:
    seconds = max(0, round(float(milliseconds) / 1000))
    minutes, remainder = divmod(seconds, 60)
    return f"{minutes}m {remainder:02d}s" if minutes else f"{remainder}s"
