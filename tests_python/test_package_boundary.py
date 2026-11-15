"""Phase 4: two import packages with one rule between them.

`skolekit` reads any school on SkoleIntra and never imports the household's
product, `skoleklar`. The old `agskoleintra` import package has been removed.
"""
import ast
import importlib
import os
import re
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

from skolekit import sources
from skolekit.transport import READ_POSTS

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


def imported(path: Path) -> set[str]:
    """Every module a file imports, at the top or inside a function."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            found |= {f"{node.module}.{alias.name}" for alias in node.names}
    return found


def test_skolekit_never_imports_the_product():
    offenders = {
        str(path.relative_to(SRC)): sorted(name for name in imported(path)
                                            if name.split(".")[0] in {"skoleklar", "agskoleintra"})
        for path in sorted((SRC / "skolekit").rglob("*.py"))
    }
    assert {name: found for name, found in offenders.items() if found} == {}


def test_skolekit_ships_no_household_or_school():
    # No school host, product name or household setting in the toolkit, code or data.
    pattern = re.compile(r"[a-z0-9-]+\.m\.skoleintra\.dk|skoleklar|agskoleintra|ag-school|ag-skoleintra|AG_"
                         r"|AG-SkoleIntra")
    found = [str(path.relative_to(SRC)) for path in sorted((SRC / "skolekit").rglob("*"))
             if path.is_file() and path.suffix in {".py", ".toml"}
             and pattern.search(path.read_text(encoding="utf-8"))]
    assert found == []


def test_the_old_import_package_is_removed():
    assert not list((SRC / "agskoleintra").glob("*.py"))
    # Nothing in the runtime, tests or rendering scripts imports the old name.
    users = []
    for root in ("src/skolekit", "src/skoleklar", "tests_python", "rendering", "prototypes"):
        for path in sorted((ROOT / root).rglob("*.py")):
            if any(name.split(".")[0] == "agskoleintra" for name in imported(path)):
                users.append(str(path.relative_to(ROOT)))
    assert users == []


def test_the_deployed_job_command_runs_the_job(monkeypatch):
    from skoleklar.runtime import scan

    calls = []
    monkeypatch.setattr(scan, "run", lambda runtime: calls.append(runtime.name) or {"status": "complete"})
    monkeypatch.delitem(sys.modules, "skoleklar.cloud_job", raising=False)
    runpy.run_module("skoleklar.cloud_job", run_name="__main__")
    assert calls == ["gcp"]


@pytest.mark.parametrize("entry", ["skoleklar.delivery_api", "skoleklar.browser_api"])
def test_the_deployed_services_find_their_app(entry):
    assert importlib.import_module(entry).app is not None


def test_the_sources_are_the_published_pages_and_the_harvest_adds_messages():
    from skoleklar.harvest import HARVEST_ORDER

    assert [source.name for source in sources.SOURCES] == ["frontpage", "calendar", "weekly_plan", "forms"]
    assert HARVEST_ORDER == ("frontpage", "messages", "calendar", "weekly_plan", "forms")


def test_skolekit_stores_nothing_and_calls_no_model():
    # Decision 10: no database code and no model client in the toolkit.
    offenders = {
        str(path.relative_to(SRC)): sorted(name for name in imported(path)
                                            if name.split(".")[0] in {"sqlite3", "openai", "anthropic"}
                                            or "derivation" in name or "message_events" in name)
        for path in sorted((SRC / "skolekit").rglob("*.py"))
    }
    assert {name: found for name, found in offenders.items() if found} == {}


def test_the_clients_post_allowlist_is_what_the_sources_declare():
    declared = frozenset().union(*(source.read_posts for source in sources.SOURCES))
    assert declared == READ_POSTS


def test_skolekit_alone_uses_its_own_names_and_the_product_sets_its_own(tmp_path):
    import subprocess

    probe = ("import skolekit.state as s{extra}; "
             "print(s.state_dir(), s.keychain_service(), s.settings().username_env)")
    env = {**os.environ, "SKOLEKIT_HOME": str(tmp_path / "kit"), "AG_SCHOOL_HOME": str(tmp_path / "product")}
    alone = subprocess.run([sys.executable, "-c", probe.format(extra="")], env=env, capture_output=True,
                           text=True, check=True).stdout.split()
    assert alone == [str((tmp_path / "kit").resolve()), "skolekit", "SKOLEKIT_USERNAME"]
    product = subprocess.run([sys.executable, "-c", probe.format(extra="; import skoleklar")], env=env,
                             capture_output=True, text=True, check=True).stdout.split()
    assert product == [str((tmp_path / "product").resolve()), "ag-skoleintra", "AG_SCHOOL_USERNAME"]


def test_the_product_command_is_skoleklar_only():
    import tomllib

    scripts = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["scripts"]
    assert scripts == {"skoleklar": "skoleklar.cli:main", "skolekit": "skolekit.cli:main"}


def test_the_guard_refuses_a_deployed_gateway_hostname_and_allows_invented_ones():
    from tests_python.household_guard import personal_traces

    deployed = "agskoleintra-delivery-" + "q7wz2k9d" + ".ew.gateway.dev"
    assert personal_traces(f'GATEWAY_HOST = "{deployed}"') == ["a gateway hostname"]
    for invented in ("example.ew.gateway.dev", "agskoleintra-delivery-example.ew.gateway.dev",
                     "https://example.gateway.dev", "GATEWAY_HOST.gateway.dev"):
        assert personal_traces(invented) == []


def test_the_household_is_in_no_tracked_file():
    # The owner's identifiers, from the owner's files: never written into this test.
    from tests_python.household_guard import offenders

    # Without the owner's files the guard still refuses any real address or home path.
    # Every tracked file: the code, the prompts, the tests and fixtures, the docs, the
    # firmware and the deploy scripts. The household's details live only in its own files.
    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True)
    # The guard itself is left out: its list of month names is not about anyone.
    found = offenders([ROOT / name for name in tracked.stdout.splitlines()
                       if (ROOT / name).is_file() and name != "tests_python/household_guard.py"])
    assert found == []
