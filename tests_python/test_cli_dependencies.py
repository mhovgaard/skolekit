"""The base CLI works without browser, renderer, server, cloud or MCP libraries."""
import os
import subprocess
import sys

from skoleklar.storage import SnapshotStore


def test_base_commands_work_when_optional_libraries_cannot_be_imported(tmp_path):
    database = tmp_path / "school.db"
    with SnapshotStore(database):
        pass
    script = '''
import importlib.abc
import sys

OPTIONAL = {"playwright", "PIL", "google", "fastapi", "uvicorn", "mcp"}
class WithoutExtras(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in OPTIONAL:
            raise ModuleNotFoundError("Optional library deliberately unavailable", name=fullname)
sys.meta_path.insert(0, WithoutExtras())
from skoleklar.cli import build_parser
assert "skoleklar.pipeline" not in sys.modules
assert "skoleklar.cli.lifecycle" not in sys.modules
parser = build_parser()
for command in (["runtime", "show", "--json"],
                ["week", "--db", sys.argv[1], "--from", "2026-11-16"],
                ["db", "query", "last-scan", "--db", sys.argv[1], "--json"]):
    args = parser.parse_args(command)
    assert args.handler(args) == 0
assert not OPTIONAL.intersection(name.split(".")[0] for name in sys.modules)
assert "skoleklar.pipeline" not in sys.modules
'''
    result = subprocess.run([sys.executable, "-c", script, str(database)], env=os.environ,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_missing_browser_extra_has_an_installation_hint():
    script = '''
import importlib.abc
import sys
class WithoutBrowser(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] == "playwright":
            raise ModuleNotFoundError("Browser deliberately unavailable", name=fullname)
sys.meta_path.insert(0, WithoutBrowser())
from skoleklar.cli import main
sys.argv = ["skoleklar", "login"]
main()
'''
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert result.returncode == 2
    assert "uv sync --extra browser" in result.stderr
    assert "Traceback" not in result.stderr
