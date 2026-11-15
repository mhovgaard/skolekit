"""`skoleklar serve`: the browser board on this machine, for the local runtime.

The board the local scans draw, from the household's `display/` folder, with
the same page as in the cloud. Three things replace the cloud's parts:

- **the viewer check:** a request from this machine's loopback address is the
  owner. Nothing else is accepted, and the server listens on loopback only;
- **the host check:** the request must name `localhost` or `127.0.0.1`. A web
  page that points its own domain at this machine (DNS rebinding) comes from
  loopback too, but under its own name, and is refused;
- **the refresh control:** a small file store in `control/` with the same
  generation rules as the control bucket;
- **the dispatcher:** the refresh button starts `skoleklar scan` as its own
  process, which records its result in `control/operations/`.

The kit opens no port to the network. Serving the board, or the phone's
delivery API, to other devices needs HTTPS and device tokens, and is not built.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

from skoleklar.delivery_store import LocalDeliveryStore
from skoleklar.refresh_control import Conflict, RefreshController

LOOPBACK = {"127.0.0.1", "::1"}
LOCAL_HOSTS = ["localhost", "127.0.0.1"]
OPERATION = re.compile(r"local/[0-9a-f]{32}")


class LocalControlStore:
    """The control bucket's read/put/delete with generations, as files in one folder."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    @contextlib.contextmanager
    def _exclusive(self):
        with open(self.root / ".control.lock", "a", encoding="utf-8") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def _path(self, name: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_./-]+\.json", name) or ".." in name:
            raise ValueError("Invalid control object name")
        return self.root / name

    def _current(self, name: str) -> tuple[Any, int]:
        path = self._path(name)
        if not path.exists():
            return None, 0
        record = json.loads(path.read_text(encoding="utf-8"))
        return record["value"], int(record["generation"])

    def read(self, name):
        with self._exclusive():
            return self._current(name)

    def put(self, name, value, generation):
        with self._exclusive():
            _, current = self._current(name)
            if current != generation:
                raise Conflict()
            path = self._path(name)
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            staged = path.with_name(f".{path.name}.{os.getpid()}.tmp")
            staged.write_text(json.dumps({"generation": current + 1, "value": value}), encoding="utf-8")
            staged.chmod(0o600)
            os.replace(staged, path)
            return current + 1

    def delete(self, name, generation):
        with self._exclusive():
            _, current = self._current(name)
            if current and current == generation:
                self._path(name).unlink(missing_ok=True)


class LocalScanDispatcher:
    """Starts `skoleklar scan` as its own process; its result lands in `control/operations/`."""

    def __init__(self, home: Path, *, popen=subprocess.Popen):
        self.home = Path(home)
        self.popen = popen

    def _result(self, operation: str) -> Path:
        return self.home / "control" / "operations" / f"{operation.split('/')[1]}.json"

    def start(self) -> str:
        operation = f"local/{uuid.uuid4().hex}"
        self.popen([sys.executable, "-m", "skoleklar.runtime.serve", "run-scan", operation],
                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   start_new_session=True, env={**os.environ, "AG_SCHOOL_HOME": str(self.home)})
        return operation

    def status(self, operation: str) -> str:
        if not OPERATION.fullmatch(str(operation)):
            raise ValueError("Invalid operation")
        path = self._result(operation)
        if not path.exists():
            return "running"
        return json.loads(path.read_text(encoding="utf-8"))["status"]


def local_viewer(request) -> str | None:
    """This machine's own browser is the owner; nothing else is a viewer."""
    client = getattr(request, "client", None)
    return "local" if client is not None and client.host in LOOPBACK else None


def create_local_app(home: Path, *, port: int, dispatcher=None):
    from starlette.middleware.trustedhost import TrustedHostMiddleware

    from skoleklar.browser_api import create_app

    control = LocalDeliveryStore(Path(home) / "control")
    controller = RefreshController(LocalControlStore(Path(home) / "control"),
                                   dispatcher or LocalScanDispatcher(home))
    app = create_app(store=LocalDeliveryStore(Path(home) / "display"), status_store=control,
                     controller=controller, viewer=local_viewer, owners=["local"], viewers=[],
                     origin={f"http://localhost:{port}", f"http://127.0.0.1:{port}"})
    # Outermost, so another site's name for this machine is refused before anything is read.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=LOCAL_HOSTS)
    return app


def serve(home: Path, *, port: int) -> None:
    import uvicorn

    uvicorn.run(create_local_app(home, port=port), host="127.0.0.1", port=port, log_level="warning",
                access_log=False)


def run_scan(operation: str) -> int:
    """The refresh button's process: one local scan, then its result for the page."""
    from skoleklar import cli

    if not OPERATION.fullmatch(operation):
        return 2
    args = cli.build_parser().parse_args(["scan"])
    try:
        code = args.handler(args)
    except Exception:  # noqa: BLE001  (any failure must still reach the page as 'failed')
        code = 1
    result = LocalScanDispatcher(Path(os.environ["AG_SCHOOL_HOME"]))._result(operation)
    result.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    result.write_text(json.dumps({"status": "complete" if code == 0 else "failed"}), encoding="utf-8")
    return code


if __name__ == "__main__" and len(sys.argv) == 3 and sys.argv[1] == "run-scan":
    raise SystemExit(run_scan(sys.argv[2]))
