"""The GCP bootstrap's message-handle key: reuse, create only when safe, refuse otherwise."""
import contextlib
import importlib.util
import sqlite3
from pathlib import Path

import pytest

from skoleklar.storage import SnapshotStore

SCRIPT = Path(__file__).resolve().parents[1] / "infra/gcp/bootstrap-dashboard.py"


@pytest.fixture
def bootstrap():
    spec = importlib.util.spec_from_file_location("bootstrap_dashboard", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Keyring:
    def __init__(self, values=None):
        self.values = dict(values or {})

    def get_password(self, service, account):
        return self.values.get((service, account))

    def set_password(self, service, account, value):
        self.values[(service, account)] = value


def database(tmp_path: Path) -> Path:
    path = tmp_path / "school-restricted.db"
    with SnapshotStore(path):
        pass
    return path


def test_every_handle_table_exists_in_the_schema(bootstrap, tmp_path):
    path = database(tmp_path)
    with contextlib.closing(sqlite3.connect(path)) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert set(bootstrap.MESSAGE_TABLES) | {"item_workflow"} <= tables


def test_an_existing_keychain_key_is_used_unchanged(bootstrap, tmp_path, capsys):
    keyring = Keyring({("ag-skoleintra", "message-handle-key"): "existing"})
    assert bootstrap.local_handle_key(keyring, database(tmp_path)) == "existing"
    assert keyring.values == {("ag-skoleintra", "message-handle-key"): "existing"}
    assert "existing" not in capsys.readouterr().out


def test_a_database_without_handles_gets_a_new_key_kept_in_the_keychain(bootstrap, tmp_path, capsys):
    keyring = Keyring()
    key = bootstrap.local_handle_key(keyring, database(tmp_path))
    assert len(key) >= 32
    assert keyring.values == {("ag-skoleintra", "message-handle-key"): key}
    assert key not in capsys.readouterr().out


def test_a_missing_database_holds_no_handles(bootstrap, tmp_path):
    assert not bootstrap.holds_message_handles(tmp_path / "absent.db")


@pytest.mark.parametrize("statement", [
    "INSERT INTO message_thread_state (thread_ref, student_id, child, participant_display_json, exists_now,"
    " server_unread, is_new_thread, has_new_message, latest_unretrieved, inbox_position, latest_message_ref,"
    " observed_at, policy_version) VALUES ('t', 1, 'A', '[]', 1, 0, 0, 0, 0, 0, 'm', '2026-01-01', 'v')",
    "INSERT INTO item_workflow (stable_key, first_seen_at, last_seen_at)"
    " VALUES ('message-event:" + "a" * 64 + "', '2026-01-01', '2026-01-01')",
])
def test_a_database_with_handles_and_no_key_is_refused(bootstrap, tmp_path, statement):
    path = database(tmp_path)
    with contextlib.closing(sqlite3.connect(path)) as connection:
        connection.execute(statement)
        connection.commit()
    keyring = Keyring()
    with pytest.raises(ValueError, match="refusing to generate a different key"):
        bootstrap.local_handle_key(keyring, path)
    assert keyring.values == {}
