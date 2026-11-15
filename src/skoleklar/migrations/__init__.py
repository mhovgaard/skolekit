"""Numbered schema migrations for the private SQLite store.

The rules are in docs/data-foundation.md, "Migrations". Each `NNNN_name.sql` file in this
package runs once, in one transaction, and is recorded in `schema_migrations`
with the sha256 of its bytes. There are no down migrations: going back means
restoring an earlier bucket generation.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.resources import files
from typing import Callable

_FILE_NAME = re.compile(r"(\d{4})_([a-z0-9_]+)\.sql")

BOOTSTRAP = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    checksum   TEXT NOT NULL,
    applied_at TEXT NOT NULL
) STRICT;
"""


class SchemaError(RuntimeError):
    """The database and the code disagree about the schema; nothing was changed."""


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    sql: str
    checksum: str


def migrations() -> tuple[Migration, ...]:
    found: list[Migration] = []
    for entry in files(__package__).iterdir():
        match = _FILE_NAME.fullmatch(entry.name)
        if match is None:
            continue
        data = entry.read_bytes()
        found.append(Migration(int(match.group(1)), match.group(2),
                               data.decode("utf-8"), hashlib.sha256(data).hexdigest()))
    found.sort(key=lambda migration: migration.version)
    if [migration.version for migration in found] != list(range(1, len(found) + 1)):
        raise SchemaError("Migration numbers must run 1, 2, 3 … without gaps")
    return tuple(found)


def latest_version() -> int:
    return len(migrations())


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _user_tables(connection: sqlite3.Connection) -> set[str]:
    return {row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")}


def schema_shape(connection: sqlite3.Connection) -> dict[str, frozenset]:
    """Tables with their columns (name, type, not null, primary key) and the index names.

    Column order and defaults are left out: columns added later with ALTER TABLE
    sit at the end of their table and carry a default the baseline doesn't.
    """
    shape: dict[str, frozenset] = {}
    for table in sorted(_user_tables(connection) - {"schema_migrations"}):
        shape[table] = frozenset(
            (row[1], row[2].upper(), bool(row[3]), bool(row[5]))
            for row in connection.execute(f'PRAGMA table_info("{table}")'))
    shape["<indexes>"] = frozenset(row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'index' AND sql IS NOT NULL"))
    return shape


def baseline_shape() -> dict[str, frozenset]:
    reference = sqlite3.connect(":memory:")
    try:
        reference.executescript(migrations()[0].sql)
        return schema_shape(reference)
    finally:
        reference.close()


def _differences(actual: dict[str, frozenset], expected: dict[str, frozenset]) -> list[str]:
    problems = [f"missing table {name}" for name in sorted(expected.keys() - actual.keys())]
    problems += [f"unexpected table {name}" for name in sorted(actual.keys() - expected.keys())]
    problems += [f"table {name} differs" for name in sorted(expected.keys() & actual.keys())
                 if expected[name] != actual[name]]
    return problems


def _record(connection: sqlite3.Connection, migration: Migration) -> None:
    connection.execute(
        "INSERT INTO schema_migrations(version, name, checksum, applied_at) VALUES (?, ?, ?, ?)",
        (migration.version, migration.name, migration.checksum, _now()))


def _apply(connection: sqlite3.Connection, migration: Migration) -> None:
    # executescript() commits first and then runs in autocommit, so the
    # migration brings its own transaction; the bookkeeping row goes inside it.
    # The values are fixed formats (a number, [a-z0-9_], hex, a timestamp).
    record = (f"INSERT INTO schema_migrations(version, name, checksum, applied_at) "
              f"VALUES ({migration.version}, '{migration.name}', '{migration.checksum}', '{_now()}');")
    try:
        connection.executescript(f"BEGIN;\n{migration.sql}\n;{record}\nCOMMIT;")
    except Exception:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise


def check_versions(connection: sqlite3.Connection) -> None:
    """Refuse a database that is newer than the code or whose migrations were edited."""
    known = {migration.version: migration for migration in migrations()}
    for version, name, checksum in connection.execute(
            "SELECT version, name, checksum FROM schema_migrations ORDER BY version"):
        migration = known.get(version)
        if migration is None:
            raise SchemaError(
                f"The database is at schema version {version}, newer than this code "
                f"({len(known)}); use newer code or restore an earlier generation")
        if migration.checksum != checksum or migration.name != name:
            raise SchemaError(f"Migration {version:04d}_{name} differs from the one applied")


def migrate(connection: sqlite3.Connection, *,
            legacy_upgrade: Callable[[sqlite3.Connection], None] | None = None) -> list[int]:
    """Bring the database to the latest version; returns the versions applied.

    A database made before migrations existed (tables, no `schema_migrations`)
    is first given the old startup upgrade, then marked as version 1 if its
    tables match the baseline. Anything else is refused untouched.
    """
    pending = migrations()
    tables = _user_tables(connection)
    applied: list[int] = []
    if "schema_migrations" not in tables:
        if tables:
            if legacy_upgrade is not None:
                legacy_upgrade(connection)
                connection.commit()
            problems = _differences(schema_shape(connection), baseline_shape())
            if problems:
                raise SchemaError("The database does not match the baseline schema: "
                                  + "; ".join(problems[:5]))
            connection.executescript(BOOTSTRAP)
            _record(connection, pending[0])
            connection.commit()
        else:
            connection.executescript(BOOTSTRAP)
    check_versions(connection)
    done = {row[0] for row in connection.execute("SELECT version FROM schema_migrations")}
    for migration in pending:
        if migration.version not in done:
            _apply(connection, migration)
            applied.append(migration.version)
    return applied


def current_version(connection: sqlite3.Connection) -> int:
    if "schema_migrations" not in _user_tables(connection):
        return 0
    row = connection.execute("SELECT max(version) FROM schema_migrations").fetchone()
    return int(row[0] or 0)
