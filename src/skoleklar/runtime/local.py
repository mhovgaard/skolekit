"""The local runtime: a scan on the parent's machine, with its state in the household's folder.

The data foundation's "work on a copy, upload all or nothing", with a rename
in place of the upload:

- a lock file in the household's folder lets one scan run at a time;
- the database is copied into a private working folder beside it (SQLite's
  backup, so a half-written WAL is copied whole) and the scan works there;
- once the invariants pass, the copy replaces the database by rename. If the
  database or the session changed while the scan ran, nothing is replaced.

The phone manifest, the board's frames, the board's status reports, the run
summary and the board's private composition files go to folders beside the
database: `delivery/`, `display/`, `control/` and `dashboard/`. `skoleklar
serve` reads them from there.
"""
from __future__ import annotations

import contextlib
import json
import os
import shutil
import sqlite3
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from skolekit.profile import CONFIG_FILES
from skoleklar.weekend_art import ARTWORK_FOLDER
from skolekit.state import state_dir
from skoleklar.delivery_store import LocalDeliveryStore
from skoleklar.household import load_site_profile
from skoleklar.meaning import MEANING_FILE
from skoleklar.restricted_messages import (
    resolve_message_mode,
    resolve_message_processor,
)
from skoleklar.runtime.config import load_runtime_config
from skoleklar.runtime.scan import DATABASE, SESSION, ScanSettings
from skoleklar.triage.derivation import DEFAULT_MODEL
from skoleklar.triage.prompt_manifest import TRIAGE_FILE

LOCK = "scan.lock"


class ScanLocked(RuntimeError):
    """Another scan holds the household's lock."""


class StateChanged(RuntimeError):
    """The database or session changed while the scan ran; nothing was replaced."""


def _fingerprint(path: Path) -> tuple[int, int, int, int] | None:
    """What changes when something writes the file: its size and time, and its WAL's.

    An empty WAL is the same as none: a reader may create one, only a writer fills it.
    """
    if not path.exists():
        return None
    main = path.stat()
    wal = path.with_name(path.name + "-wal")
    wal_stat = wal.stat() if wal.exists() and wal.stat().st_size else None
    return (main.st_size, main.st_mtime_ns,
            wal_stat.st_size if wal_stat else 0, wal_stat.st_mtime_ns if wal_stat else 0)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class LocalRuntime:
    name = "local"

    def __init__(self, home: Path | None = None, *, model: str | None = None,
                 message_mode: str | None = None, message_processor: str | None = None) -> None:
        self.home = Path(home or state_dir()).expanduser().resolve()
        self.model, self.message_mode, self.message_processor = model, message_mode, message_processor
        self.delivery = LocalDeliveryStore(self.home / "delivery")

    def start(self) -> ScanSettings:
        config = load_runtime_config(self.home)
        model = self.model or os.environ.get("AG_LLM_MODEL") or config.model or DEFAULT_MODEL
        timezone = ZoneInfo(load_site_profile(self.home).school.timezone)
        return ScanSettings(model, resolve_message_mode(self.message_mode),
                            resolve_message_processor(self.message_processor), timezone, uuid.uuid4().hex)

    def workspace(self):
        # Beside the database, so the commit is a rename on one file system.
        return tempfile.TemporaryDirectory(prefix=".scan-", dir=self.home)

    def checkout(self, state_dir: Path) -> dict[str, Any]:
        token: dict[str, Any] = {}
        database = self.home / DATABASE
        token[DATABASE] = _fingerprint(database)
        if database.exists():
            with contextlib.closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as source, \
                    contextlib.closing(sqlite3.connect(state_dir / DATABASE)) as target:
                source.backup(target)
        session = self.home / SESSION
        token[SESSION] = _fingerprint(session)
        for name in (SESSION, *CONFIG_FILES, MEANING_FILE, TRIAGE_FILE):
            if (self.home / name).exists():
                shutil.copy2(self.home / name, state_dir / name)
        # The household's own house pictures, when it keeps any.
        if (self.home / ARTWORK_FOLDER).is_dir():
            shutil.copytree(self.home / ARTWORK_FOLDER, state_dir / ARTWORK_FOLDER, dirs_exist_ok=True)
        return token

    def commit(self, state_dir: Path, token: dict[str, Any]) -> None:
        for name in (DATABASE, SESSION):
            if _fingerprint(self.home / name) != token[name]:
                raise StateChanged(f"{name} in {self.home} changed while the scan ran; nothing was replaced")
        staged = []
        for name in (DATABASE, SESSION):
            if not (state_dir / name).exists():
                continue
            # Copy, not move: the scan still reads its copy to publish.
            target = self.home / f".{name}.commit"
            shutil.copyfile(state_dir / name, target)
            target.chmod(0o600)
            staged.append((target, self.home / name))
        for target, destination in staged:
            if destination.name == DATABASE:
                # The old WAL belongs to the old file; its frames are in the copy already.
                for suffix in ("-wal", "-shm"):
                    destination.with_name(destination.name + suffix).unlink(missing_ok=True)
            os.replace(target, destination)

    def display_store(self) -> LocalDeliveryStore:
        return LocalDeliveryStore(self.home / "display")

    def status_store(self) -> LocalDeliveryStore:
        return LocalDeliveryStore(self.home / "control")

    def _private(self, name: str) -> Path:
        folder = self.home / "dashboard"
        folder.mkdir(mode=0o700, exist_ok=True)
        path = folder / name
        # A nested name, such as an eval capture's evals/DATE/RUN.jsonl, gets its folders.
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        return path

    def read_private(self, name: str) -> bytes | None:
        path = self._private(name)
        return path.read_bytes() if path.exists() else None

    def write_private(self, name: str, text: str) -> None:
        path = self._private(name)
        staged = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        staged.write_text(text, encoding="utf-8")
        staged.chmod(0o600)
        os.replace(staged, path)

    def write_control(self, name: str, document: dict[str, Any]) -> None:
        LocalDeliveryStore(self.home / "control").write_json(name, document)

    @contextlib.contextmanager
    def lock(self):
        path = self.home / LOCK
        self.home.mkdir(parents=True, exist_ok=True, mode=0o700)
        record = json.dumps({"pid": os.getpid(), "started_at": datetime.now(UTC).isoformat()})
        for _ in range(2):
            try:
                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                break
            except FileExistsError:
                try:
                    holder = json.loads(path.read_text(encoding="utf-8"))
                    alive = _pid_alive(int(holder["pid"]))
                except (OSError, ValueError, KeyError, TypeError):
                    alive = True   # unreadable: assume a scan is starting
                if alive:
                    raise ScanLocked(f"Another scan holds {path}") from None
                path.unlink(missing_ok=True)   # left by a scan that died
        else:
            raise ScanLocked(f"Another scan holds {path}")
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(record)
        try:
            yield
        finally:
            path.unlink(missing_ok=True)
