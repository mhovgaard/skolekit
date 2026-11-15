"""Command helpers for runtime."""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from skoleklar.household import load_site_profile
from skolekit.profile import ProfileError
from skoleklar.cli.common import _runtime_config_or_report, config_root


def _weather_from_config(config) -> None:
    """The household's place for the forecast, unless the environment already names one."""
    if config.weather is not None and not os.environ.get("AG_WEATHER_PLACE"):
        os.environ["AG_WEATHER_PLACE"] = config.weather.place
        os.environ["AG_WEATHER_LATITUDE"] = str(config.weather.latitude)
        os.environ["AG_WEATHER_LONGITUDE"] = str(config.weather.longitude)


def command_scan(args: argparse.Namespace) -> int:
    """One scan with every stage the cloud job runs, on this machine (the local runtime)."""
    from skoleklar.runtime import scan
    from skoleklar.runtime.config import WrongWriter, require_writer
    from skoleklar.runtime.local import LocalRuntime, ScanLocked

    config = _runtime_config_or_report()
    if config is None:
        return 2
    try:
        # Before the lock and before any copy: a household that runs on GCP never scans here.
        require_writer(config, "local")
    except WrongWriter as error:
        print(str(error), file=sys.stderr)
        return 2
    if config.where is None:
        print("`skoleklar scan` makes this machine the household's one writer. Say so first: "
              "[runtime] where = \"local\" in skoleklar.toml (or \"gcp\" if the cloud scans).",
              file=sys.stderr)
        return 2
    _weather_from_config(config)
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    runtime = LocalRuntime(config_root(), model=args.model, message_mode=args.message_mode,
                           message_processor=args.message_processor)
    try:
        result = scan.run(runtime)
    except ScanLocked as error:
        print(f"{error}: a scan is already running.", file=sys.stderr)
        return 2
    except Exception as error:
        scan._event("sync_failed", error_type=type(error).__name__)
        print(f"The scan stopped: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    return 0 if result["status"] == "complete" else 1


def command_serve(args: argparse.Namespace) -> int:
    """The browser board on this machine, from the local runtime's folders; loopback only."""
    from skoleklar.runtime.serve import serve

    config = _runtime_config_or_report()
    if config is None:
        return 2
    if config.where == "gcp":
        print("skoleklar.toml says the household runs on GCP; its board is the cloud's browser board.",
              file=sys.stderr)
        return 2
    print(f"The board is at http://localhost:{args.port}/ on this machine only. Ctrl+C stops it.",
          file=sys.stderr)
    serve(config_root(), port=args.port)
    return 0


def command_schedule(args: argparse.Namespace) -> int:
    """The scan times as a launchd job or systemd timer: show, install or remove."""
    import platform

    from skoleklar.runtime import schedule

    config = _runtime_config_or_report()
    if config is None:
        return 2
    home = config_root()
    if args.schedule_command == "show":
        if platform.system() == "Darwin":
            print(schedule.launchd_plist(config, home).decode("utf-8"), end="")
        else:
            for name, text in schedule.systemd_units(config, home).items():
                print(f"# {name}\n{text}")
        return 0
    if args.schedule_command == "remove":
        removed = schedule.remove()
        print("Removed " + ", ".join(map(str, removed)) if removed else "No schedule was installed.")
        return 0
    if config.where != "local":
        print("Set [runtime] where = \"local\" in skoleklar.toml first: a schedule makes this machine "
              "the household's one writer.", file=sys.stderr)
        return 2
    written = schedule.install(config, home)
    print("Scans at " + ", ".join(scan.text() for scan in config.scans) + "; installed "
          + ", ".join(map(str, written)))
    return 0


def command_runtime_show(args: argparse.Namespace) -> int:
    """Where and how the household runs, from skoleklar.toml; the deploy scripts read --json."""
    config = _runtime_config_or_report()
    if config is None:
        return 2
    document = config.document()
    try:
        document["timezone"] = load_site_profile().school.timezone
    except ProfileError:
        document["timezone"] = None   # no school.toml yet
    if args.json:
        print(json.dumps(document, ensure_ascii=False, indent=2))
        return 0
    print(f"runtime   {document['where'] or 'not set (local or gcp)'}")
    print(f"timezone  {document['timezone'] or '(no school.toml)'}")
    print(f"model     {document['model'] or '(default)'}")
    print("scans     " + ", ".join(scan["text"] for scan in document["scans"]))
    weather = document["weather"]
    print(f"weather   {weather['place'] if weather else 'none'}")
    gcp = document["gcp"]
    print(f"gcp       {gcp['project'] or '-'} in {gcp['region']}; "
          f"{len(gcp['owners'])} owner(s), {len(gcp['viewers'])} viewer(s)")
    return 0
