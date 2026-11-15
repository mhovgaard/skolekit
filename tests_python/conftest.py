"""Shared test setup."""
import pytest

from tests_python.synthetic_household import write_household


@pytest.fixture(autouse=True)
def _private_state_dir(monkeypatch, tmp_path_factory):
    """Each test gets its own state directory holding only the invented household's
    school.toml and profile.toml (tests_python/synthetic_household.py), so a
    developer's own files or session never reach a test. A test that needs other
    files writes them, or sets AG_SCHOOL_HOME itself. skolekit on its own reads
    SKOLEKIT_HOME, which gets an empty directory."""
    home = write_household(tmp_path_factory.mktemp("ag-school-home"))
    monkeypatch.setenv("AG_SCHOOL_HOME", str(home))
    monkeypatch.setenv("SKOLEKIT_HOME", str(tmp_path_factory.mktemp("skolekit-home")))
