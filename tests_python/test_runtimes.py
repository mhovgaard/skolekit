"""Phase 10: one scan, two runtimes.

The same household scanned by the GCP runtime (the pinned cloud scan's
in-memory buckets) and by the local runtime (a folder) gives the same events,
service requests, database rows, phone manifest, board and run summary, apart
from the run's ID and its working paths. A scan stops before any work when the
household's config names the other runtime as the writer, and the local
runtime replaces nothing when the database changed under it.
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import socket
import sqlite3
from pathlib import Path

import pytest

from skoleklar import cli
from skoleklar.runtime import scan as scan_service
from skoleklar.runtime.config import WrongWriter, load_runtime_config, parse_scan
from skoleklar.runtime.local import LocalRuntime, ScanLocked, StateChanged
from tests_python.test_cloud_scan_pinned import REFRESH_ID, Scan

LOGGER = "agskoleintra.cloud_job"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("network access in a runtime test")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


@pytest.fixture
def scan(monkeypatch, tmp_path):
    harness = Scan(monkeypatch, tmp_path)
    harness.seed = dict(harness.gcs.objects)
    return harness


def local_home(scan, home: Path) -> Path:
    """The household's folder holding exactly what the cloud's buckets held before the scan."""
    def place(bucket: str, name: str) -> Path:
        if bucket == "private":
            for prefix in ("state/restricted-v1/", "state/"):
                if name.startswith(prefix):
                    return home / name.removeprefix(prefix)
            return home / name                      # dashboard/…
        return home / bucket / name                 # delivery/…, control/…
    for (bucket, name), (data, _) in scan.seed.items():
        path = place(bucket, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return home


def run(scan, caplog, runtime):
    scan.commands, scan.calls = [], []
    caplog.clear()
    with caplog.at_level(logging.INFO, logger=LOGGER):
        result = scan_service.run(runtime)
    events = [json.loads(record.getMessage()) for record in caplog.records if record.name == LOGGER]
    return result, events


def normal(value, ids):
    """The value with the run's ID and working paths made the same for both runtimes."""
    text = json.dumps(value, sort_keys=True, default=str)
    for run_id in ids:
        text = text.replace(run_id, "RUN")
    return json.loads(text)


def without_paths(commands):
    return [(name, {key: value for key, value in request.items() if key != "db"}) for name, request in commands]


def rows(database: Path) -> dict[str, list]:
    """Every table's rows, without the times they were written."""
    with contextlib.closing(sqlite3.connect(database)) as connection:
        tables = [name for (name,) in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        result = {}
        for table in tables:
            columns = [row[1] for row in connection.execute(f"PRAGMA table_info({table})")]
            kept = [c for c in columns if not c.endswith("_at") and c not in {"at", "applied", "started", "completed"}]
            result[table] = sorted(map(tuple, connection.execute(
                f"SELECT {', '.join(kept)} FROM {table}").fetchall()), key=repr)
        return result


def folder_json(folder: Path) -> dict[str, object]:
    return {path.relative_to(folder).as_posix(): json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(folder.rglob("*.json"))}


# --- The same scan on both runtimes ------------------------------------------------------------------

def test_a_local_scan_and_a_cloud_scan_give_the_same_result(scan, caplog, tmp_path):
    from skoleklar.runtime.gcp import GcpRuntime

    cloud_result, cloud_events = run(scan, caplog, GcpRuntime())
    cloud_commands, cloud_calls = scan.commands, scan.calls
    cloud_database = tmp_path / "cloud.db"
    cloud_database.write_bytes(scan.gcs.objects[("private", "state/restricted-v1/school-restricted.db")][0])

    home = local_home(scan, tmp_path / "local-home")
    local_result, local_events = run(scan, caplog, LocalRuntime(home))
    ids = [REFRESH_ID, local_result["refresh_id"]]

    assert normal(local_result, ids) == normal(cloud_result, ids)
    assert normal(local_events, ids) == normal(cloud_events, ids)
    assert without_paths(scan.commands) == without_paths(cloud_commands)
    assert normal(scan.calls, ids) == normal(cloud_calls, ids)
    assert rows(home / "school-restricted.db") == rows(cloud_database)
    # The phone manifest, the board, the private composition files and the run summary.
    cloud = {(bucket, name): json.loads(data) for (bucket, name), (data, _) in scan.gcs.objects.items()
             if name.endswith(".json")}
    assert folder_json(home / "delivery") == {name: value for (bucket, name), value in cloud.items()
                                              if bucket == "delivery"}
    assert normal(folder_json(home / "display"), ids) == normal(
        {name: value for (bucket, name), value in cloud.items() if bucket == "display"}, ids)
    assert folder_json(home / "dashboard") == {name.removeprefix("dashboard/"): value
                                               for (bucket, name), value in cloud.items()
                                               if bucket == "private" and name.startswith("dashboard/")}
    assert normal(folder_json(home / "control"), ids) == normal(
        {name: value for (bucket, name), value in cloud.items() if bucket == "control"}, ids)
    assert local_result["status"] == "complete"
    # The working copy is gone; the lock is released; the database has no stale WAL.
    assert sorted(path.name for path in home.iterdir() if path.name.startswith(".")) == []
    assert not (home / "scan.lock").exists()


# --- One writer -------------------------------------------------------------------------------------

def test_a_local_scan_stops_while_the_config_says_gcp(scan, caplog, tmp_path):
    home = local_home(scan, tmp_path / "local-home")
    (home / "skoleklar.toml").write_text('[runtime]\nwhere = "gcp"\n', encoding="utf-8")
    before = (home / "school-restricted.db").read_bytes()
    with pytest.raises(WrongWriter, match="second writer"):
        run(scan, caplog, LocalRuntime(home))
    assert scan.commands == []
    assert (home / "school-restricted.db").read_bytes() == before
    assert json.loads((home / "control" / "latest-run.json").read_text())["error_type"] == "WrongWriter"


def test_a_cloud_scan_stops_while_the_config_says_local(scan, caplog):
    from skoleklar.runtime.gcp import GcpRuntime

    scan.gcs.put("private", "state/skoleklar.toml", '[runtime]\nwhere = "local"\n')
    with pytest.raises(WrongWriter):
        run(scan, caplog, GcpRuntime())
    assert scan.commands == []
    assert not [line for line in scan.gcs.trail if " upload " in line]


def test_skoleklar_scan_refuses_before_the_lock_while_the_config_says_gcp(tmp_path, monkeypatch, capsys):
    home = Path(os.environ["AG_SCHOOL_HOME"])
    (home / "skoleklar.toml").write_text('[runtime]\nwhere = "gcp"\n', encoding="utf-8")
    args = cli.build_parser().parse_args(["scan"])
    assert args.handler(args) == 2
    assert "second writer" in capsys.readouterr().err
    assert not (home / "scan.lock").exists()


# --- All or nothing ---------------------------------------------------------------------------------

def test_nothing_is_replaced_when_the_database_changed_during_the_scan(scan, caplog, tmp_path, monkeypatch):
    home = local_home(scan, tmp_path / "local-home")
    from skoleklar import pipeline

    derive = pipeline.derive

    def derive_while_someone_writes(request):
        with contextlib.closing(sqlite3.connect(home / "school-restricted.db")) as connection:
            connection.execute("CREATE TABLE another_writer (x)")
            connection.commit()
        return derive(request)

    monkeypatch.setattr(pipeline, "derive", derive_while_someone_writes)
    with pytest.raises(StateChanged, match="nothing was replaced"):
        run(scan, caplog, LocalRuntime(home))
    with contextlib.closing(sqlite3.connect(home / "school-restricted.db")) as connection:
        assert connection.execute("SELECT name FROM sqlite_master WHERE name = 'another_writer'").fetchone()
    assert not (home / "delivery" / "current.json").exists()


def test_one_local_scan_at_a_time_and_a_dead_scans_lock_is_taken_over(tmp_path):
    runtime = LocalRuntime(tmp_path)
    with runtime.lock(), pytest.raises(ScanLocked), LocalRuntime(tmp_path).lock():
        pass
    (tmp_path / "scan.lock").write_text(json.dumps({"pid": 2 ** 22 + 12345, "started_at": "x"}))
    with runtime.lock():
        assert json.loads((tmp_path / "scan.lock").read_text())["pid"] == os.getpid()
    assert not (tmp_path / "scan.lock").exists()


# --- The household's settings ----------------------------------------------------------------------

def test_the_runtime_settings_and_their_defaults(tmp_path):
    assert [scan.cron() for scan in load_runtime_config(tmp_path).scans] == ["30 5 * * *"]
    (tmp_path / "skoleklar.toml").write_text(
        '[runtime]\nwhere = "local"\nscans = ["sat-sun 09:00", "fri 15:30"]\n'
        '[weather]\nplace = "Byen"\nlatitude = 55.6789\nlongitude = 12.5678\n'
        '[gcp]\nproject = "my-household-1"\nowners = ["Parent@Example.com"]\n'
        '[meaning.bookings]\ndefault_minutes = 15\n', encoding="utf-8")
    config = load_runtime_config(tmp_path)
    assert (config.where, [s.cron() for s in config.scans]) == ("local", ["0 9 * * 0,6", "30 15 * * 5"])
    assert (config.weather.latitude, config.gcp.owners) == (55.68, ("parent@example.com",))
    for bad in ('[runtime]\nwhere = "both"\n', '[runtime]\nscans = ["25:00"]\n', '[weather]\nplace = "x"\n',
                '[gcp]\nowners = ["not an address"]\n', '[runtime]\ncolour = "blue"\n'):
        (tmp_path / "skoleklar.toml").write_text(bad, encoding="utf-8")
        with pytest.raises(Exception, match="skoleklar.toml"):
            load_runtime_config(tmp_path)
    assert parse_scan("mon-fri 05:45").text() == "mon-fri 05:45"


def test_skoleklar_scan_needs_the_household_to_name_its_writer(capsys):
    # A household that never said where it runs could be scanning in the cloud: no local scan.
    args = cli.build_parser().parse_args(["scan"])
    assert args.handler(args) == 2
    assert 'where = "local"' in capsys.readouterr().err


def test_the_older_writers_refuse_while_the_config_says_gcp(tmp_path, capsys):
    home = Path(os.environ["AG_SCHOOL_HOME"])
    (home / "skoleklar.toml").write_text('[runtime]\nwhere = "gcp"\n', encoding="utf-8")
    for argv in (["harvest"], ["sync"], ["derive"], ["consolidate", "--as-of", "2026-11-12"],
                 ["item", "acknowledge", "1"], ["brief", "--date", "2026-11-12", "--record-delivery"]):
        args = cli.build_parser().parse_args(argv)
        assert args.handler(args) == 2, argv
        assert "second writer" in capsys.readouterr().err, argv
    # Reading, a dry run, or another file is not a second writer.
    for argv in (["derive", "--dry-run"], ["brief", "--date", "2026-11-12"],
                 ["item", "acknowledge", "1", "--db", str(tmp_path / "scratch.db")]):
        args = cli.build_parser().parse_args(argv)
        with contextlib.suppress(Exception, SystemExit):
            args.handler(args)
        assert "second writer" not in capsys.readouterr().err, argv


# --- The local schedule -----------------------------------------------------------------------------

def test_the_scan_times_as_launchd_and_systemd_units(tmp_path):
    import plistlib

    from skoleklar.runtime import schedule

    config = load_runtime_config(tmp_path)
    plist = plistlib.loads(schedule.launchd_plist(config, tmp_path))
    assert plist["ProgramArguments"][-2:] == ["skoleklar.cli", "scan"]
    assert plist["EnvironmentVariables"] == {"AG_SCHOOL_HOME": str(tmp_path)}
    assert plist["StartCalendarInterval"] == [{"Hour": 5, "Minute": 30}]
    timer = schedule.systemd_units(config, tmp_path)["skoleklar-scan.timer"]
    assert [line for line in timer.splitlines() if line.startswith("OnCalendar")] == [
        "OnCalendar=*-*-* 05:30:00"]


def test_a_schedule_is_installed_only_for_the_local_runtime(capsys):
    home = Path(os.environ["AG_SCHOOL_HOME"])
    (home / "skoleklar.toml").write_text('[runtime]\nwhere = "gcp"\n', encoding="utf-8")
    args = cli.build_parser().parse_args(["scan-schedule", "install"])
    assert args.handler(args) == 2
    assert "one writer" in capsys.readouterr().err


# --- The local board --------------------------------------------------------------------------------

def test_the_local_board_answers_this_machine_only(tmp_path):
    from fastapi.testclient import TestClient

    from skoleklar.runtime.serve import create_local_app

    started = []

    class Dispatcher:
        def start(self):
            started.append(True)
            return "local/" + "a" * 32

        def status(self, operation):
            return "running"

    app = create_local_app(tmp_path, port=8787, dispatcher=Dispatcher())
    assert TestClient(app, base_url="http://localhost:8787",
                      client=("192.168.1.20", 50000)).get("/").status_code == 401
    # Another site's name for this machine (DNS rebinding) is refused, even from loopback.
    rebound = TestClient(app, base_url="http://attacker.example:8787", client=("127.0.0.1", 50000))
    assert rebound.get("/").status_code == 400
    assert TestClient(app, base_url="http://127.0.0.1:8787",
                      client=("127.0.0.1", 50000)).get("/").status_code == 503
    local = TestClient(app, base_url="http://localhost:8787", client=("127.0.0.1", 50000))
    page = local.get("/")
    assert page.status_code == 503 and "Opdater" in page.text   # no board drawn yet; the owner can refresh
    form = dict(re.findall(r"type='hidden' name='([a-z_]+)' value='([^']+)'", page.text))
    local.cookies.set("ag_csrf", form["csrf"])
    response = local.post("/admin/refresh", data=form, headers={"Origin": "http://localhost:8787"},
                          follow_redirects=False)
    assert response.status_code in (302, 303) and started == [True]


def test_the_local_control_store_keeps_the_buckets_generation_rules(tmp_path):
    from skoleklar.refresh_control import Conflict
    from skoleklar.runtime.serve import LocalControlStore

    store = LocalControlStore(tmp_path)
    assert store.read("dispatch-lock.json") == (None, 0)
    first = store.put("dispatch-lock.json", {"request": "a"}, 0)
    with pytest.raises(Conflict):
        store.put("dispatch-lock.json", {"request": "b"}, 0)
    second = store.put("dispatch-lock.json", {"request": "b"}, first)
    assert store.read("dispatch-lock.json") == ({"request": "b"}, second)
    store.delete("dispatch-lock.json", first)        # an old generation deletes nothing
    assert store.read("dispatch-lock.json")[0] == {"request": "b"}
    store.delete("dispatch-lock.json", second)
    assert store.read("dispatch-lock.json") == (None, 0)


def test_the_refresh_button_starts_a_local_scan_and_reads_its_result(tmp_path):
    from skoleklar.runtime.serve import LocalScanDispatcher

    calls = []
    dispatcher = LocalScanDispatcher(tmp_path, popen=lambda argv, **options: calls.append((argv, options)))
    operation = dispatcher.start()
    argv, options = calls[0]
    assert argv[-2:] == ["run-scan", operation] and options["start_new_session"]
    assert options["env"]["AG_SCHOOL_HOME"] == str(tmp_path)
    assert dispatcher.status(operation) == "running"
    (tmp_path / "control" / "operations").mkdir(parents=True)
    (tmp_path / "control" / "operations" / f"{operation.split('/')[1]}.json").write_text('{"status": "failed"}')
    assert dispatcher.status(operation) == "failed"
    with pytest.raises(ValueError):
        dispatcher.status("projects/x/operations/y")


# --- The deploy scripts read the household -----------------------------------------------------------

def test_the_deploy_scripts_read_the_households_settings(tmp_path, capsys):
    import shutil
    import subprocess

    if not shutil.which("jq") or not shutil.which("bash"):
        pytest.skip("jq and bash are needed")
    home = Path(os.environ["AG_SCHOOL_HOME"])
    (home / "skoleklar.toml").write_text(
        '[runtime]\nwhere = "gcp"\nmodel = "provider/model-2"\nscans = ["mon-fri 06:15", "19:30"]\n'
        '[weather]\nplace = "Byen"\nlatitude = 55.6789\nlongitude = 12.5678\n'
        '[gcp]\nowners = ["a@example.com", "b@example.com"]\n', encoding="utf-8")
    args = cli.build_parser().parse_args(["runtime", "show", "--json"])
    assert args.handler(args) == 0
    document = capsys.readouterr().out
    assert json.loads(document)["timezone"] == "Europe/Copenhagen"
    # `uv run skoleklar runtime show --json`, stood in for by its output.
    bin_folder = tmp_path / "bin"
    bin_folder.mkdir()
    (bin_folder / "uv").write_text(f"#!/usr/bin/env bash\ncat <<'JSON'\n{document}JSON\n")
    (bin_folder / "uv").chmod(0o755)
    script = Path(__file__).parents[1] / "infra" / "gcp" / "household-settings.sh"
    probe = (f'source "{script}"; printf "%s\\n" "$HOUSEHOLD_TIMEZONE" "$HOUSEHOLD_MODEL" '
             '"$HOUSEHOLD_WEATHER_PLACE" "$HOUSEHOLD_WEATHER_LATITUDE" "$HOUSEHOLD_OWNERS" "$HOUSEHOLD_SCANS"')
    environment = {key: value for key, value in os.environ.items() if not key.startswith(("AG_WEATHER", "AG_LLM"))}
    result = subprocess.run(["bash", "-c", probe], capture_output=True, text=True, check=True,
                            env={**environment, "PATH": f"{bin_folder}:{os.environ['PATH']}"})
    assert result.stdout.splitlines() == [
        "Europe/Copenhagen", "provider/model-2", "Byen", "55.68", "a@example.com,b@example.com",
        "15 6 * * 1-5|mon-fri 06:15", "30 19 * * *|19:30"]
