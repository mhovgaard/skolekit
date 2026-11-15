"""`skoleklar scan-schedule`: the household's scan times as a launchd job (macOS) or a systemd timer.

The times come from `[runtime] scans` in skoleklar.toml, the same list the GCP
skill gives Cloud Scheduler. Each run is `skoleklar scan` with the household's
folder; its log goes to `logs/scan.log` there. A schedule is installed only
for the local runtime: while the config says `gcp`, the cloud is the writer.
The e-ink board's own wake times are in its firmware (`wake_plan.h`), so they
follow the cloud's scans, not these.
"""
from __future__ import annotations

import platform
import plistlib
import subprocess
import sys
from pathlib import Path

from skoleklar.runtime.config import RuntimeConfig, ScanTime

LABEL = "dk.skoleklar.scan"
UNIT = "skoleklar-scan"
SYSTEMD_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def program(home: Path) -> list[str]:
    return [sys.executable, "-m", "skoleklar.cli", "scan"]


def launchd_plist(config: RuntimeConfig, home: Path) -> bytes:
    intervals = []
    for scan in config.scans:
        if scan.every_day:
            intervals.append({"Hour": scan.hour, "Minute": scan.minute})
        else:
            # launchd counts Monday as 1 and Sunday as 7.
            intervals += [{"Weekday": day + 1, "Hour": scan.hour, "Minute": scan.minute} for day in scan.days]
    log = str(home / "logs" / "scan.log")
    return plistlib.dumps({
        "Label": LABEL,
        "ProgramArguments": program(home),
        "EnvironmentVariables": {"AG_SCHOOL_HOME": str(home)},
        "StartCalendarInterval": intervals,
        "StandardOutPath": log,
        "StandardErrorPath": log,
        "ProcessType": "Background",
    })


def _on_calendar(scan: ScanTime) -> str:
    days = "" if scan.every_day else (
        SYSTEMD_DAYS[scan.days[0]] + (f"..{SYSTEMD_DAYS[scan.days[-1]]}" if len(scan.days) > 1 else "") + " ")
    return f"{days}*-*-* {scan.hour:02d}:{scan.minute:02d}:00"


def systemd_units(config: RuntimeConfig, home: Path) -> dict[str, str]:
    command = " ".join(program(home))
    service = ("[Unit]\nDescription=skoleklar: one scan of the household's school\n\n"
               f"[Service]\nType=oneshot\nEnvironment=AG_SCHOOL_HOME={home}\nExecStart={command}\n"
               f"StandardOutput=append:{home / 'logs' / 'scan.log'}\n"
               f"StandardError=append:{home / 'logs' / 'scan.log'}\n")
    timer = ("[Unit]\nDescription=skoleklar: the household's scan times\n\n[Timer]\n"
             + "".join(f"OnCalendar={_on_calendar(scan)}\n" for scan in config.scans)
             + "Persistent=false\n\n[Install]\nWantedBy=timers.target\n")
    return {f"{UNIT}.service": service, f"{UNIT}.timer": timer}


def _launch_agent() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def _systemd_folder() -> Path:
    return Path.home() / ".config" / "systemd" / "user"


def install(config: RuntimeConfig, home: Path, *, run=subprocess.run, system: str | None = None) -> list[Path]:
    """Write the unit and load it; returns what was written."""
    (home / "logs").mkdir(parents=True, exist_ok=True, mode=0o700)
    if (system or platform.system()) == "Darwin":
        path = _launch_agent()
        path.parent.mkdir(parents=True, exist_ok=True)
        run(["launchctl", "unload", str(path)], check=False, capture_output=True)
        path.write_bytes(launchd_plist(config, home))
        run(["launchctl", "load", str(path)], check=True)
        return [path]
    folder = _systemd_folder()
    folder.mkdir(parents=True, exist_ok=True)
    written = []
    for name, text in systemd_units(config, home).items():
        (folder / name).write_text(text, encoding="utf-8")
        written.append(folder / name)
    run(["systemctl", "--user", "daemon-reload"], check=True)
    run(["systemctl", "--user", "enable", "--now", f"{UNIT}.timer"], check=True)
    return written


def remove(*, run=subprocess.run, system: str | None = None) -> list[Path]:
    """Unload and delete the unit; returns what was removed."""
    if (system or platform.system()) == "Darwin":
        path = _launch_agent()
        if not path.exists():
            return []
        run(["launchctl", "unload", str(path)], check=False, capture_output=True)
        path.unlink()
        return [path]
    folder = _systemd_folder()
    run(["systemctl", "--user", "disable", "--now", f"{UNIT}.timer"], check=False, capture_output=True)
    removed = [folder / name for name in (f"{UNIT}.service", f"{UNIT}.timer") if (folder / name).exists()]
    for path in removed:
        path.unlink()
    run(["systemctl", "--user", "daemon-reload"], check=False, capture_output=True)
    return removed
