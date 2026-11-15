"""Phase 3: the scan's stages are services with typed requests and results.

The cloud job and `skoleklar sync` call the services directly. The CLI's only
use of argparse is parsing its own flags. The services report through their
results, never through stdout, so no caller parses printed JSON.
"""
import argparse
import ast
import asyncio
import json
from pathlib import Path

import pytest

from skoleklar import pipeline
from skoleklar.cli import lifecycle as cli
from skoleklar.pipeline import (
    ArchiveRequest,
    ConsolidateRequest,
    DeriveRequest,
    HarvestRequest,
    ManifestRequest,
    StageResult,
)
from skoleklar.storage import SnapshotStore

SRC = Path(__file__).resolve().parents[1] / "src"


def tree(name: str) -> ast.Module:
    return ast.parse((SRC / name).read_text(encoding="utf-8"))


def imported_modules(module: ast.Module) -> set[str]:
    found = set()
    for node in ast.walk(module):
        if isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            found |= {f"{node.module}.{alias.name}" for alias in node.names}
    return found


# --- The guards ----------------------------------------------------------------------------

def test_no_module_builds_a_namespace():
    # argparse.Namespace objects come only from the CLI's own parser.
    builders = [path.name for path in sorted(SRC.rglob("*.py"))
                if "argparse.Namespace(" in path.read_text(encoding="utf-8")]
    assert builders == []


@pytest.mark.parametrize("name", ["skoleklar/cloud_job.py", "skoleklar/runtime/scan.py", "skoleklar/runtime/gcp.py",
                                  "skoleklar/runtime/local.py", "skoleklar/pipeline.py",
                                  "skolekit/auth/browser.py"])
def test_the_cloud_job_and_the_services_never_reach_the_cli(name):
    modules = imported_modules(tree(name))
    assert "argparse" not in modules
    # `from skoleklar import cli` shows up as skoleklar.cli too.
    assert not {module for module in modules if module.startswith("skoleklar.cli")}


def test_the_services_never_print_to_stdout():
    for node in ast.walk(tree("skoleklar/pipeline.py")):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "print":
            assert any(keyword.arg == "file" for keyword in node.keywords), node.lineno


def test_the_cloud_job_reads_fields_not_printed_json():
    source = (SRC / "skoleklar/runtime/scan.py").read_text(encoding="utf-8")
    assert "stdout.getvalue()" not in source and "json.loads(output)" not in source
    for command in ("command_harvest", "command_derive", "command_archive", "command_consolidate"):
        assert command not in source


# --- The services and their CLI wrappers ---------------------------------------------------

def test_a_stage_without_its_database_is_refused(tmp_path, capsys):
    result = pipeline.consolidate(ConsolidateRequest(as_of="2026-11-13", db=tmp_path / "missing.db"))
    assert result == StageResult(2) and not result.ok
    assert "No snapshot database" in capsys.readouterr().err


def test_the_cli_prints_what_the_service_returns(tmp_path, capsys):
    database = tmp_path / "school-restricted.db"
    with SnapshotStore(database):
        pass
    request = ManifestRequest(date="2026-11-13", db=database, dry_run=True,
                              message_mode="events-only", message_processor="zdr")
    result = pipeline.manifest(request)
    assert result.ok and result.output["calendar"] == []
    assert capsys.readouterr().out == ""

    args = argparse.Namespace(db=str(database), date="2026-11-13", delivery_root=None,
                              delivery_bucket=None, dry_run=True, message_mode="events-only",
                              message_processor="zdr")
    assert cli.command_manifest(args) == 0
    printed = json.loads(capsys.readouterr().out)
    timed = {"generated_at", "expires_at", "manifest_id"}
    assert {key: value for key, value in printed.items() if key not in timed} == {
        key: value for key, value in result.output.items() if key not in timed}


def test_sync_runs_the_five_services_in_order_with_typed_requests(monkeypatch, tmp_path, capsys):
    calls = []

    async def harvest(request):
        calls.append(request)
        return StageResult(0, {"status": "complete"})

    def stage(request):
        calls.append(request)
        return StageResult(0, {})

    monkeypatch.setattr(cli, "harvest", harvest)
    for name in ("derive", "archive", "consolidate", "manifest"):
        monkeypatch.setattr(cli, name, stage)
    args = argparse.Namespace(db=str(tmp_path / "school.db"), date="2026-11-13", mode="daily",
                              model="synthetic/model-1", message_mode="events-only",
                              message_processor="zdr", message_concurrency=2, progress="off",
                              delivery_root=str(tmp_path / "delivery"), delivery_bucket=None)
    assert asyncio.run(cli.command_sync(args)) == 0
    assert [type(call) for call in calls] == [HarvestRequest, DeriveRequest, ArchiveRequest,
                                              ConsolidateRequest, ManifestRequest]
    harvest_request, derive_request, archive_request, consolidate_request, manifest_request = calls
    assert harvest_request.auto_login and harvest_request.mode == "daily"
    assert archive_request.as_of == consolidate_request.as_of == manifest_request.date == "2026-11-13"
    assert consolidate_request.limit == 200
    assert {call.db for call in calls} == {Path(args.db).resolve()}
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [line.get("stage") for line in lines] == ["harvest", "derive", "archive", "consolidate",
                                                     "manifest", None]


def test_sync_stops_at_the_first_failed_service(monkeypatch, tmp_path, capsys):
    async def harvest(request):
        return StageResult(0, {})

    monkeypatch.setattr(cli, "harvest", harvest)
    monkeypatch.setattr(cli, "derive", lambda request: StageResult(2, {"error": "x"}))
    monkeypatch.setattr(cli, "archive", lambda request: pytest.fail("archive ran after a failure"))
    args = argparse.Namespace(db=str(tmp_path / "school.db"), date="2026-11-13", mode="daily",
                              model="m", message_mode="events-only", message_processor="zdr",
                              message_concurrency=2, progress="off", delivery_root=None,
                              delivery_bucket=None)
    assert asyncio.run(cli.command_sync(args)) == 2
    assert "Sync stopped because derive failed." in capsys.readouterr().err
