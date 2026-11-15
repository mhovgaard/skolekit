"""Command helpers for lifecycle."""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import fields as dataclass_fields
from datetime import date
from pathlib import Path
from time import monotonic
from typing import Any
from skoleklar.pipeline import (
    ArchiveRequest,
    ConsolidateRequest,
    DeriveRequest,
    HarvestRequest,
    ManifestRequest,
    StageResult,
    archive,
    consolidate,
    derive,
    harvest,
    manifest,
    snapshot_db_path,
)
from skoleklar.progress import _elapsed_text, _emit_progress
from skoleklar.storage import SnapshotStore


def _request(request_type: Any, args: argparse.Namespace) -> Any:
    """A service request from the parsed flags: the CLI's only use of the Namespace."""
    return request_type(**{field.name: getattr(args, field.name)
                           for field in dataclass_fields(request_type) if hasattr(args, field.name)})


def _print_result(result: StageResult) -> int:
    if result.output is not None:
        print(json.dumps(result.output, ensure_ascii=False, indent=2))
    return result.exit_code


async def command_harvest(args: argparse.Namespace) -> int:
    return _print_result(await harvest(_request(HarvestRequest, args)))


def command_derive(args: argparse.Namespace) -> int:
    return _print_result(derive(_request(DeriveRequest, args)))


def command_archive(args: argparse.Namespace) -> int:
    return _print_result(archive(_request(ArchiveRequest, args)))


def command_consolidate(args: argparse.Namespace) -> int:
    return _print_result(consolidate(_request(ConsolidateRequest, args)))


def command_manifest(args: argparse.Namespace) -> int:
    return _print_result(manifest(_request(ManifestRequest, args)))


async def command_sync(args: argparse.Namespace) -> int:
    message_mode = getattr(args, "message_mode", None)
    message_processor = getattr(args, "message_processor", None)
    database = (
        Path(args.db).expanduser().resolve()
        if args.db
        else snapshot_db_path()
    )
    try:
        date.fromisoformat(args.date)
    except ValueError:
        print("--date must be an ISO date such as 2026-08-24.", file=sys.stderr)
        return 2
    stage_started = monotonic()
    result = await harvest(HarvestRequest(
        db=database,
        mode=args.mode,
        message_mode=message_mode,
        message_processor=message_processor,
        message_concurrency=getattr(args, "message_concurrency", 2),
        progress=getattr(args, "progress", "auto"),
        model=args.model,
        auto_login=True,
        auto_login_timeout=2,
    ))
    if not result.ok:
        print("Sync stopped because harvest failed.", file=sys.stderr)
        return result.exit_code
    duration_ms = round((monotonic() - stage_started) * 1000)
    print(json.dumps({"stage": "harvest", "status": "complete", "duration_ms": duration_ms}))

    stages = [
        ("derive", lambda: derive(DeriveRequest(
            db=database, model=args.model, message_mode=message_mode))),
        ("archive", lambda: archive(ArchiveRequest(
            as_of=args.date, db=database, model=args.model, message_mode=message_mode))),
        ("consolidate", lambda: consolidate(ConsolidateRequest(
            as_of=args.date, db=database, limit=200, model=args.model, message_mode=message_mode))),
        ("manifest", lambda: manifest(ManifestRequest(
            date=args.date, db=database, delivery_root=args.delivery_root,
            delivery_bucket=args.delivery_bucket, message_mode=message_mode,
            message_processor=message_processor))),
    ]
    for name, run in stages:
        stage_started = monotonic()
        _emit_progress(args, name.capitalize())
        result = run()
        if not result.ok:
            print(f"Sync stopped because {name} failed.", file=sys.stderr)
            return result.exit_code
        duration_ms = round((monotonic() - stage_started) * 1000)
        _emit_progress(
            args,
            name.capitalize(),
            detail=_elapsed_text(duration_ms),
            done=True,
        )
        print(json.dumps({"stage": name, "status": "complete", "duration_ms": duration_ms}))
    print(json.dumps({"status": "complete", "date": args.date}))
    return 0


def command_item(args: argparse.Namespace) -> int:
    database = Path(args.db).expanduser().resolve() if args.db else snapshot_db_path()
    if not database.exists():
        print("No snapshot database.", file=sys.stderr)
        return 2
    with SnapshotStore(database) as store:
        result = store.update_item_workflow(args.item_id, args.item_action)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0
