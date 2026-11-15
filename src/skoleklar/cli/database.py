"""Command helpers for database."""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import sqlite3
import sys
from importlib.resources import files
from pathlib import Path
from skoleklar import invariants
from skoleklar.records_copy import (
    CopyError,
    copy_path,
    open_copy,
    pull_copy,
    require_current,
    run_saved_query,
)


def _private_bucket_name(args: argparse.Namespace) -> str:
    if getattr(args, "bucket", None):
        return args.bucket
    if os.environ.get("AG_PRIVATE_BUCKET"):
        return os.environ["AG_PRIVATE_BUCKET"]
    project = (getattr(args, "project", None) or os.environ.get("GOOGLE_CLOUD_PROJECT")
               or os.environ.get("CLOUDSDK_CORE_PROJECT"))
    if project:
        return f"{project}-ag-private-state"
    raise ValueError("Name the bucket with --bucket or --project, or set AG_PRIVATE_BUCKET")


def _open_copy(args: argparse.Namespace) -> tuple[Path, sqlite3.Connection]:
    """A read-only connection to a copy that nothing writes while it is open."""
    path = copy_path(args.db)
    return path, open_copy(path)


def command_db_pull(args: argparse.Namespace) -> int:
    """Download the cloud database's current generation, unless it is the one we have."""
    print(json.dumps(pull_copy(bucket=_private_bucket_name(args), project=args.project,
                               target=copy_path(args.db))))
    return 0


def _print_rows(columns: list[str], rows: list[tuple], *, as_json: bool, width: int = 60) -> None:
    if as_json:
        print(json.dumps([dict(zip(columns, row)) for row in rows], ensure_ascii=False, indent=2))
        return
    if not rows:
        print("(no rows)")
        return
    cells = [["" if value is None else str(value) for value in row] for row in rows]
    cells = [[value if len(value) <= width else value[: width - 1] + "…" for value in row]
             for row in cells]
    widths = [max(len(column), *(len(row[index]) for row in cells))
              for index, column in enumerate(columns)]
    print("  ".join(column.ljust(widths[index]) for index, column in enumerate(columns)).rstrip())
    print("  ".join("-" * size for size in widths))
    for row in cells:
        print("  ".join(value.ljust(widths[index]) for index, value in enumerate(row)).rstrip())


def command_db_query(args: argparse.Namespace) -> int:
    if not args.term and ":term" in files("skoleklar").joinpath("queries", f"{args.name}.sql").read_text(
            encoding="utf-8"):
        raise ValueError(f"`db query {args.name}` needs a search term")
    path, connection = _open_copy(args)
    with contextlib.closing(connection):
        try:
            require_current(connection, path)
        except CopyError as error:
            print(str(error), file=sys.stderr)
            return 2
        columns, rows = run_saved_query(connection, args.name, args.term)
        _print_rows(columns, rows, as_json=args.json)
    return 0


def command_db_check(args: argparse.Namespace) -> int:
    """Run the invariant checks on a copy; exit 1 when one fails."""
    _, connection = _open_copy(args)
    with contextlib.closing(connection):
        failures = invariants.check(connection)
    names = ["schema versions match the code", "schema is at the code's version",
             "integrity_check is ok", *invariants.CHECKS]
    failed = dict(failures)
    stopped = any(name in failed for name in names[:2])
    for name in names:
        if name in failed:
            print(f"FAIL  {name}: {failed[name]}")
        elif not stopped:
            print(f"pass  {name}")
    return 1 if failures else 0


def command_mcp(args: argparse.Namespace) -> int:
    """Serve the household's records from a pulled copy as MCP tools over stdio, until the client closes it."""
    try:
        from skoleklar.mcp.server import build_server
    except ImportError:
        print("The MCP server needs the mcp extra: `uv sync --extra mcp`.", file=sys.stderr)
        return 2
    path = copy_path(args.db)
    if args.pull:
        from google.api_core.exceptions import GoogleAPIError
        from google.auth.exceptions import GoogleAuthError

        # Standard output is the protocol's channel from here on; the pull reports to stderr.
        try:
            print(json.dumps(pull_copy(bucket=_private_bucket_name(args), project=args.project, target=path)),
                  file=sys.stderr)
        except (GoogleAPIError, GoogleAuthError, OSError, ValueError) as error:
            if not path.exists():
                print(f"The pull failed and there is no copy to serve: {error}", file=sys.stderr)
                return 2
            print(f"The pull failed; serving the copy already here: {error}", file=sys.stderr)
    if not path.exists():
        print(f"No database at {path}; run `skoleklar db pull` first, or start with --pull.", file=sys.stderr)
        return 2
    confirmations = Path(args.event_confirmations).expanduser() if args.event_confirmations else None
    build_server(path, confirmations=confirmations).run("stdio")
    return 0
