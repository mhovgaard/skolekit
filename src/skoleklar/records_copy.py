"""The household's records as a read-only copy: where it is, how it is pulled, the saved queries.

`skoleklar db` and `skoleklar mcp` both use these, so a tool and its CLI twin
run the same SQL on the same kind of connection. A copy is opened
`mode=ro&immutable=1`: nothing writes it while it is open, so SQLite takes no
locks and leaves no `-wal` or `-shm` file beside it. A pull replaces the file
whole, so a reader that opens it per call sees the new copy at its next call.
"""
from __future__ import annotations

import sqlite3
import stat
from importlib.resources import files
from pathlib import Path
from typing import Any

from skolekit.state import ensure_private_dir, state_dir
from skoleklar.migrations import current_version, latest_version

# The cloud database, in the private state bucket; `db pull` copies it to cloud_db_path().
CLOUD_DB_OBJECT = "state/restricted-v1/school-restricted.db"


class CopyError(ValueError):
    """The copy is missing, or older than the code's schema."""


def cloud_db_path() -> Path:
    return state_dir() / "cloud" / "school-restricted.db"


def saved_query_names() -> list[str]:
    return sorted(entry.name.removesuffix(".sql")
                  for entry in files("skoleklar").joinpath("queries").iterdir()
                  if entry.name.endswith(".sql"))


def copy_path(db: str | Path | None) -> Path:
    """The copy to read: `db` when given, else the pulled one."""
    return Path(db).expanduser().resolve() if db else cloud_db_path()


def pull_copy(*, bucket: str, project: str | None, target: Path) -> dict[str, Any]:
    """Download the cloud database's current generation to `target`, unless it is the one there."""
    from google.cloud import storage as gcs

    ensure_private_dir(target.parent)
    blob = gcs.Client(project=project).bucket(bucket).blob(CLOUD_DB_OBJECT)
    blob.reload()
    generation = int(blob.generation)
    marker = target.with_name(target.name + ".generation")
    if target.exists() and marker.exists() and marker.read_text().strip() == str(generation):
        return {"status": "unchanged", "generation": generation, "path": str(target)}
    partial = target.with_name(target.name + ".download")
    try:
        blob.download_to_filename(partial, if_generation_match=generation)
        partial.chmod(stat.S_IRUSR | stat.S_IWUSR)
        partial.replace(target)
    finally:
        partial.unlink(missing_ok=True)
    for suffix in ("-wal", "-shm"):
        target.with_name(target.name + suffix).unlink(missing_ok=True)
    marker.write_text(f"{generation}\n", encoding="utf-8")
    marker.chmod(stat.S_IRUSR | stat.S_IWUSR)
    return {"status": "downloaded", "generation": generation, "path": str(target),
            "bytes": target.stat().st_size}


def open_copy(path: Path) -> sqlite3.Connection:
    """A read-only connection to a copy that nothing writes while it is open."""
    if not path.exists():
        raise CopyError(f"No database at {path}; run `skoleklar db pull` first")
    return sqlite3.connect(f"{path.as_uri()}?mode=ro&immutable=1", uri=True)


def require_current(connection: sqlite3.Connection, path: Path) -> None:
    """Refuse a copy older than the code's schema: the views it reads may not exist there."""
    version = current_version(connection)
    if version < latest_version():
        raise CopyError(f"{path} is at schema version {version}; the saved queries need "
                        f"version {latest_version()}. Pull again after a scan with the new code.")


def run_saved_query(connection: sqlite3.Connection, name: str,
                    term: str | None = None) -> tuple[list[str], list[tuple[Any, ...]]]:
    """The columns and rows of one saved query; `trace` needs a search term."""
    if name not in saved_query_names():
        raise ValueError(f"No saved query named {name!r}")
    sql = files("skoleklar").joinpath("queries", f"{name}.sql").read_text(encoding="utf-8")
    if ":term" in sql and not term:
        raise ValueError(f"`db query {name}` needs a search term")
    cursor = connection.execute(sql, {"term": term} if ":term" in sql else {})
    return [description[0] for description in cursor.description], cursor.fetchall()
