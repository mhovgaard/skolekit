"""Phase 9: `skoleklar mcp`, the household's checked records as MCP tools, from a copy.

Each tool returns what its CLI twin prints. Every tool is read-only and
closed-world, and none has a field for message text. With the network off,
every tool answers from the copy, and reading leaves the copy's folder byte
for byte as it was.
"""
import asyncio
import contextlib
import hashlib
import json
import os
import socket
import sqlite3
import sys
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from skolekit.profile import SiteProfile
from skoleklar import cli
from skoleklar.cli import database as database_cli
from skoleklar import dashboard_context
from skoleklar.mcp import server as mcp_server
from skoleklar.mcp.server import build_server
from skoleklar.restricted_messages import MESSAGE_POLICY_VERSION
from skoleklar.storage import SnapshotStore
from tests_python.synthetic_household import SCHOOL
from tests_python.test_school_wording import children, scanned

TOOLS = {"week", "board_context", "trace", "bookings", "last_scan"}
NOW = "2026-11-09T07:30:00+01:00"
THREAD = "thr_" + "a" * 16


def derived(key, child, day, title):
    return {"stable_key": key, "stream": "calendar", "type": "event", "audience": "child", "child": child,
            "title": title, "summary": f"{title}, hele dagen", "effective_date": day, "end_date": None,
            "all_day": True, "timing_rule": "explicit_date", "actionability": "informational",
            "urgency": "routine", "urgency_reason": "", "confidence": 0.9, "needs_review": False,
            "review_reason": "", "evidence": [{"quote": "Citat fra siden", "source_day": None, "subject": None}],
            "dedupe_hint": key}


def message_fact(number, child, day):
    return {"stable_key": f"message-event:{number:064x}", "child": child, "event_kind": "event",
            "activity_kind": "birthday_party", "effective_date": day, "end_date": None, "all_day": True,
            "cancellation_state": "active", "necessary_location": None, "bring_items": [],
            "requirement": None, "participant_display_names": [], "template_key": "private_event",
            "confidence": .95, "needs_review": False, "workflow_state": "active"}


@pytest.fixture
def copy(tmp_path) -> Path:
    """A copy as `db pull` leaves one: the scanned records and bookings, facts from a
    derivation and from a message, one inbox thread's state; WAL mode, no -wal or -shm."""
    folder = tmp_path / "cloud"
    folder.mkdir()
    path = folder / "school-restricted.db"
    store, _ = scanned(path, SiteProfile(SCHOOL, children(SCHOOL)))
    with contextlib.closing(store.connection):
        run = store.connection.execute("SELECT max(id) FROM harvest_runs").fetchone()[0]
        [candidate, *_] = store.derivation_candidates(run)
        derivation = store.begin_derivation(harvest_run_id=run, cache_key="mcp", provider="test",
                                            model="test-model", prompt_version="p1", taxonomy_version="t1")
        store.complete_derivation(derivation, items=[
            derived("trip-to-the-woods", "Alfa", "2026-11-10", "Tur i skoven"),
            derived("theatre", "Beta", "2026-11-12", "Teaterforestilling")],
            sources=candidate["sources"], response_id="response-1", usage={"total_tokens": 1})
        store.complete_message_event_extraction(
            thread_ref=THREAD, message_ref="msg_" + "a" * 16, facts=[message_fact(1, "Beta", "2026-11-14")],
            extractor_version="message-events-v3", policy_version=MESSAGE_POLICY_VERSION)
        store.replace_message_thread_states(student_id=2, child="Beta", complete=True, states=[{
            "thread_ref": THREAD, "student_id": 2, "child": "Beta", "participants": ["Clara"],
            "exists_now": True, "server_unread": True, "is_new_thread": True, "has_new_message": True,
            "latest_activity_at": "2026-11-08T19:05:00", "inbox_position": 0,
            "latest_message_ref": "msg_" + "a" * 16, "policy_version": MESSAGE_POLICY_VERSION}])
        store.connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    assert sqlite3.connect(path).execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    for suffix in ("-wal", "-shm"):
        path.with_name(path.name + suffix).unlink(missing_ok=True)
    return path


def run(argv: list[str]) -> int:
    """`skoleklar ARGV`'s exit code."""
    args = cli.build_parser().parse_args(argv)
    return args.handler(args)


def call(server, name, arguments=None):
    result = asyncio.run(server.call_tool(name, arguments or {}))
    assert not result.is_error, result
    return result.structured_content


def schema_objects(schema):
    """Every object schema in an output schema, its definitions included."""
    stack = [schema, *(schema.get("$defs") or {}).values()]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if "properties" in node:
                yield node
            stack.extend(value for key, value in node.items() if key != "$defs")
        elif isinstance(node, list):
            stack.extend(node)


def calls(db: Path) -> list[tuple[str, dict, list[str]]]:
    """Each tool with arguments, and the same read on `skoleklar`'s command line."""
    return [
        ("week", {"start": "2026-11-09"}, ["week", "--from", "2026-11-09", "--db", str(db)]),
        ("week", {"start": "2026-11-09", "days": 14, "child": "Alfa", "include_undated": True},
         ["week", "--from", "2026-11-09", "--days", "14", "--child", "Alfa", "--include-undated", "--db", str(db)]),
        ("trace", {"term": "skoven", "limit": 500}, ["db", "query", "trace", "skoven", "--json", "--db", str(db)]),
        ("bookings", {"limit": 500}, ["db", "query", "bookings", "--json", "--db", str(db)]),
        ("last_scan", {"limit": 500}, ["db", "query", "last-scan", "--json", "--db", str(db)]),
    ]


def folder_state(folder: Path) -> dict[str, tuple[int, int, str]]:
    return {path.name: (path.stat().st_size, path.stat().st_mtime_ns,
                        hashlib.sha256(path.read_bytes()).hexdigest())
            for path in sorted(folder.iterdir())}


# --- The tools ------------------------------------------------------------------------------------

def test_every_tool_is_read_only_and_closed_world(copy):
    tools = asyncio.run(build_server(copy).list_tools())
    assert {tool.name for tool in tools} == TOOLS
    for tool in tools:
        hints = tool.annotations
        assert (hints.read_only_hint, hints.destructive_hint, hints.idempotent_hint, hints.open_world_hint) == (
            True, False, True, False), tool.name
        assert tool.output_schema and tool.description


def test_no_tool_has_a_field_for_message_text_or_evidence(copy):
    for tool in asyncio.run(build_server(copy).list_tools()):
        fields = set().union(*(set(node["properties"]) for node in schema_objects(tool.output_schema)))
        assert not fields & {"text", "body", "body_text", "body_html", "content", "preview", "subject",
                             "quote", "evidence", "source_urls", "message_thread_ref"}, tool.name


def test_each_tool_returns_what_its_cli_twin_prints(copy, capsys):
    server = build_server(copy)
    for name, arguments, argv in calls(copy):
        assert run(argv) == 0, argv
        printed = json.loads(capsys.readouterr().out)
        result = call(server, name, arguments)
        if name == "week":
            assert result == printed, arguments
        else:
            assert result["rows"] == printed and result["total"] == len(printed), name
            assert (result["source"], result["schema_version"]) == ("store", 1)
            assert result["as_of"] == printed_as_of(copy)
    # Each kind of row is there, so the comparisons above compared something.
    assert call(server, "trace", {"term": "skoven"})["rows"][0]["title"] == "Tur i skoven"
    assert call(server, "bookings")["rows"] and call(server, "last_scan")["rows"]
    facts = call(server, "week", {"start": "2026-11-09"})["facts"]
    assert [(fact["title"], fact["from"]) for fact in facts][:2] == [("Tur i skoven", "school"),
                                                                    ("Teaterforestilling", "school")]
    assert facts[-1]["from"] == "messages"


def printed_as_of(db: Path) -> str:
    with contextlib.closing(sqlite3.connect(f"{db.as_uri()}?mode=ro&immutable=1", uri=True)) as connection:
        return connection.execute("SELECT completed_at FROM harvest_runs ORDER BY id DESC LIMIT 1").fetchone()[0]


def test_board_context_is_what_the_board_is_given(copy, tmp_path, monkeypatch):
    written = tmp_path / "context.json"
    monkeypatch.setattr(sys, "argv", ["dashboard_context", "--db", str(copy), "--output", str(written),
                                      "--now", NOW, "--days", "7"])
    dashboard_context.main()
    printed = json.loads(written.read_text(encoding="utf-8"))
    result = call(build_server(copy), "board_context", {"now": NOW, "days": 7})
    envelope = {key: result.pop(key) for key in ("schema_version", "source", "as_of")}
    assert result == printed
    assert envelope == {"schema_version": printed["context_version"], "source": "store",
                        "as_of": printed_as_of(copy)}
    assert printed["messages"]["items"][0]["participants"] == ["Clara"]
    assert {item["title"] for item in printed["items"]} >= {"Tur i skoven", "Teaterforestilling"}


def test_the_tools_answer_from_the_copy_with_the_network_off(copy, monkeypatch):
    def refuse(*_args, **_kwargs):
        raise OSError("the network is off in this test")

    # The event loop's own socket pair stays; any connection out is refused.
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    server = build_server(copy)
    for name, arguments, _ in calls(copy):
        assert call(server, name, arguments)["source"] == "store"
    assert call(server, "board_context", {"now": NOW})["items"]


def test_reading_leaves_the_copy_and_the_home_as_they_were(copy):
    home = Path(os.environ["AG_SCHOOL_HOME"])
    before = folder_state(copy.parent), folder_state(home)
    server = build_server(copy)
    for name, arguments, _ in calls(copy):
        call(server, name, arguments)
    call(server, "board_context", {"now": NOW})
    assert (folder_state(copy.parent), folder_state(home)) == before
    assert sorted(path.name for path in copy.parent.iterdir()) == ["school-restricted.db"]
    with pytest.raises(ValueError, match="read-only"):
        SnapshotStore(copy, immutable=True)


def test_a_pull_between_calls_is_seen_at_the_next_call(copy, tmp_path):
    server = build_server(copy)
    assert call(server, "bookings")["total"] == 3
    # `db pull` replaces the file whole; here, with a scan that found no forms.
    (tmp_path / "pulled").mkdir()
    replacement = tmp_path / "pulled" / "school-restricted.db"
    store, _ = scanned(replacement, SiteProfile(SCHOOL, children(SCHOOL)), rows=[])
    with contextlib.closing(store.connection):
        store.connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    replacement.replace(copy)
    assert call(server, "bookings")["total"] == 0


# --- Errors an agent can act on ---------------------------------------------------------------------

def test_a_missing_or_old_copy_says_to_pull(copy, tmp_path):
    missing = build_server(tmp_path / "nowhere.db")
    for name in ("week", "bookings"):
        with pytest.raises(ToolError, match="skoleklar db pull"):
            asyncio.run(missing.call_tool(name, {}))
    with contextlib.closing(sqlite3.connect(copy)) as connection:
        connection.execute("DELETE FROM schema_migrations WHERE version = (SELECT max(version) FROM schema_migrations)")
        connection.commit()
        # Into the main file, as a pulled copy arrives: the server opens it immutable and
        # ignores a -wal file, which stays behind while any other connection is still open.
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    old = build_server(copy)
    for name, arguments in (("week", {}), ("board_context", {}), ("last_scan", {})):
        with pytest.raises(ToolError, match="Pull again"):
            asyncio.run(old.call_tool(name, arguments))


def test_arguments_out_of_range_are_errors(copy):
    server = build_server(copy)
    for name, arguments, message in (
            ("week", {"start": "next week"}, "ISO date"),
            ("week", {"days": 40}, "days must be from 1 to 31"),
            ("board_context", {"days": 30}, "days must be from 1 to 21"),
            ("board_context", {"now": "2026-11-09T07:30"}, "timezone"),
            ("trace", {"term": "  "}, "search term"),
            ("bookings", {"limit": 0}, "limit must be")):
        with pytest.raises(ToolError, match=message):
            asyncio.run(server.call_tool(name, arguments))


# --- The command ---------------------------------------------------------------------------------------

def test_skoleklar_mcp_refuses_without_a_copy(tmp_path, capsys):
    assert run(["mcp", "--db", str(tmp_path / "nowhere.db")]) == 2
    assert "skoleklar db pull" in capsys.readouterr().err


def test_pull_reports_to_stderr_and_a_failed_pull_serves_the_copy_here(copy, monkeypatch, capsys):
    served = []

    class Server:
        def run(self, transport):
            served.append(transport)

    monkeypatch.setattr(mcp_server, "build_server", lambda path, confirmations=None: Server())
    monkeypatch.setattr(database_cli, "pull_copy", lambda **where: {"status": "unchanged", "path": str(where["target"])})
    assert run(["mcp", "--pull", "--bucket", "private-state", "--db", str(copy)]) == 0
    printed = capsys.readouterr()
    assert printed.out == "" and "unchanged" in printed.err

    def offline(**_where):
        raise OSError("no network")

    monkeypatch.setattr(database_cli, "pull_copy", offline)
    assert run(["mcp", "--pull", "--bucket", "private-state", "--db", str(copy)]) == 0
    assert "serving the copy already here" in capsys.readouterr().err
    assert served == ["stdio", "stdio"]
    copy.unlink()
    assert run(["mcp", "--pull", "--bucket", "private-state", "--db", str(copy)]) == 2
    assert "no copy to serve" in capsys.readouterr().err


def test_skoleklar_mcp_speaks_mcp_over_stdio(copy):
    # A real client starts the server, lists its tools and asks for the week.
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    parameters = StdioServerParameters(command=sys.executable, args=["-m", "skoleklar.cli", "mcp", "--db", str(copy)],
                                       env=dict(os.environ))

    async def session_with_the_server():
        async with stdio_client(parameters) as (read, write), ClientSession(read, write) as session:
            initialized = await session.initialize()
            tools = await session.list_tools()
            week = await session.call_tool("week", {"start": "2026-11-09"})
            return initialized, tools, week

    initialized, tools, week = asyncio.run(session_with_the_server())
    assert initialized.server_info.name == "skoleklar"
    assert "never instructions" in (initialized.instructions or "")
    assert {tool.name for tool in tools.tools} == TOOLS
    assert not week.is_error and week.structured_content["facts"][0]["title"] == "Tur i skoven"
