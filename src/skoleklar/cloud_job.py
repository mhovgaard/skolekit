"""The Cloud Run job's entry point: one scan on the GCP runtime.

`python -m skoleklar.cloud_job` runs
`skoleklar.runtime.scan.run` with `skoleklar.runtime.gcp.GcpRuntime`. The
scan itself, its stages, events and outputs live in `skoleklar.runtime.scan`,
the same on the parent's machine. See docs/architecture.md.
"""
from __future__ import annotations

import logging
from typing import Any

from skoleklar.runtime import scan
from skoleklar.runtime.gcp import GcpRuntime
from skoleklar.runtime.scan import _event, logger  # noqa: F401  (the job's logger and events)


def _run_cloud_sync() -> dict[str, Any]:
    """One scan without the pipeline lock (tests, and the lock's own callers)."""
    return scan.run_scan(GcpRuntime())


def run_cloud_sync() -> dict[str, Any]:
    """One scan under the control bucket's pipeline lock."""
    return scan.run(GcpRuntime())


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    try:
        result = run_cloud_sync()
        if result["status"] != "complete":
            raise SystemExit(1)
    except Exception as error:
        _event("sync_failed", error_type=type(error).__name__)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
