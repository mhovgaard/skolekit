"""Phase 7: `skolekit mcp`, the live view as MCP tools, on the synthetic site.

Each tool returns exactly what its CLI twin prints, every tool is read-only,
only `message_thread` returns message text, and nothing is written to disk.
"""
import asyncio
import json
import os
import sys
from pathlib import Path

import httpx
import pytest

from mcp.server.mcpserver.exceptions import ToolError
from skolekit import cli as kit_cli
from skolekit import live
from skolekit.auth.session import SessionStore
from skolekit.mcp.server import build_server
from skolekit.profile import SiteProfile
from skolekit.sources import calendar as calendar_source
from skolekit.transport import ReadOnlyClient
from tests_python.skoleintra_site import ALFA, BETA, SCHOOL, SyntheticSite, frozen_datetime

TOOLS = {"schedule", "calendar", "weekly_plan", "forms", "notices", "messages", "message_thread",
         "school_check", "session_status"}
# Each tool with arguments, and the same read on the CLI.
CALLS = [
    ("schedule", {"child": "Alfa"}, ["schedule", "--child", "Alfa"]),
    ("calendar", {"child": "Alfa", "week": "47"}, ["calendar", "--child", "Alfa", "--week", "47"]),
    ("calendar", {"child": "Beta", "week": "47", "all_classes": True},
     ["calendar", "--child", "Beta", "--week", "47", "--all-classes"]),
    ("weekly_plan", {"child": "Alfa"}, ["weekly-plan", "--child", "Alfa"]),
    ("forms", {"child": "Alfa", "state": "open"}, ["forms", "--child", "Alfa", "--state", "open"]),
    ("notices", {"child": "Alfa", "include_expired": True}, ["notices", "--child", "Alfa", "--include-expired"]),
    ("messages", {"child": "Alfa"}, ["messages", "list", "--child", "Alfa"]),
    ("messages", {"child": "Alfa", "unread_only": True}, ["messages", "list", "--child", "Alfa", "--unread"]),
    ("message_thread", {"child": "Alfa", "thread_id": 301, "last": 2},
     ["messages", "read", "301", "--child", "Alfa", "--last", "2"]),
    ("school_check", {"child": "Alfa"}, ["school", "check", "--child", "Alfa"]),
]


@pytest.fixture(autouse=True)
def frozen_now(monkeypatch):
    monkeypatch.setattr(live, "datetime", frozen_datetime())
    monkeypatch.setattr(calendar_source, "datetime", frozen_datetime())


@pytest.fixture
def site() -> SyntheticSite:
    return SyntheticSite()


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("AG_SCHOOL_HOME", str(tmp_path))
    SessionStore().save({"cookies": []})
    return tmp_path


def server_for(site, children=(ALFA, BETA)):
    return build_server(load=lambda: SiteProfile(SCHOOL, tuple(children)),
                        client=lambda state, host: ReadOnlyClient(host, transport=httpx.MockTransport(site.handle)))


def call(server, name, arguments):
    result = asyncio.run(server.call_tool(name, arguments))
    assert not result.is_error
    return result.structured_content


def schema_objects(schema):
    """Every object schema in an output schema, with its definitions resolved by walking them too."""
    stack = [schema, *(schema.get("$defs") or {}).values()]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if "properties" in node:
                yield node
            stack.extend(value for key, value in node.items() if key != "$defs")
        elif isinstance(node, list):
            stack.extend(node)


# --- The tools ------------------------------------------------------------------------------------

def test_every_tool_is_read_only_and_reaches_the_school(site):
    tools = asyncio.run(server_for(site).list_tools())
    assert {tool.name for tool in tools} == TOOLS
    for tool in tools:
        hints = tool.annotations
        assert (hints.read_only_hint, hints.destructive_hint, hints.idempotent_hint, hints.open_world_hint) == (
            True, False, True, True), tool.name
        assert tool.output_schema and tool.description


def test_only_message_thread_has_message_text_in_its_schema(site):
    tools = {tool.name: tool for tool in asyncio.run(server_for(site).list_tools())}
    thread_fields = set().union(*(set(node["properties"]) for node in schema_objects(tools["messages"].output_schema)))
    assert not thread_fields & {"text", "body", "body_text", "body_html", "content", "preview"}
    message_objects = [node for node in schema_objects(tools["message_thread"].output_schema)
                       if {"message_id", "text"} <= set(node["properties"])]
    assert message_objects
    for name, tool in tools.items():
        if name != "message_thread":
            assert not any("message_id" in node["properties"] and "text" in node["properties"]
                           for node in schema_objects(tool.output_schema)), name


def test_message_text_reaches_an_agent_only_through_message_thread(site, home):
    # Thread 301's two messages on the synthetic site.
    bodies = ["Onsdag går vi i skoven", "Tak, vi husker det"]
    server = server_for(site)
    for name, arguments, _ in CALLS:
        text = json.dumps(call(server, name, arguments), ensure_ascii=False)
        if name == "message_thread":
            assert all(body in text for body in bodies)
        else:
            assert not any(body in text for body in bodies), name


def test_each_tool_returns_what_its_cli_twin_prints(site, home, monkeypatch, capsys):
    (home / "school.toml").write_text(f'[site]\nhost = "{SCHOOL.host}"\n', encoding="utf-8")
    (home / "profile.toml").write_text(
        '[[children]]\nstudent_id = 9101\nname = "Alfa"\ncalendar_class_name = "001"\n\n'
        '[[children]]\nstudent_id = 9202\nname = "Beta"\ncalendar_class_name = "003"\n', encoding="utf-8")
    monkeypatch.setattr(kit_cli, "school_client",
                        lambda state, host: ReadOnlyClient(host, transport=httpx.MockTransport(site.handle)))
    server = build_server(client=lambda state, host: ReadOnlyClient(host, transport=httpx.MockTransport(site.handle)))
    for name, arguments, argv in CALLS:
        assert kit_cli.main(argv) == 0, argv
        printed = json.loads(capsys.readouterr().out)
        assert call(server, name, arguments) == printed, name


def test_the_tools_write_nothing(site, home):
    before = sorted(str(path) for path in Path(home).rglob("*"))
    server = server_for(site)
    for name, arguments, _ in CALLS:
        call(server, name, arguments)
    call(server, "session_status", {})
    assert sorted(str(path) for path in Path(home).rglob("*")) == before
    assert {request.method for request in site.requests} == {"GET"}


# --- Errors an agent can act on ---------------------------------------------------------------------

def test_a_missing_or_expired_session_says_to_log_in(site, home):
    server = server_for(site)
    site.redirect_to_login(ALFA, "messages/conversations")
    with pytest.raises(ToolError, match="session has expired"):
        asyncio.run(server.call_tool("messages", {"child": "Alfa"}))
    (home / "auth-state.json").unlink()
    with pytest.raises(ToolError, match="skolekit auth login"):
        asyncio.run(server.call_tool("schedule", {"child": "Alfa"}))
    assert call(server, "session_status", {}) == {"saved": False, "valid": None}


def test_a_child_or_week_that_cant_be_read_is_an_error(site, home):
    server = server_for(site)
    with pytest.raises(ToolError, match="Choose --child: Alfa, Beta"):
        asyncio.run(server.call_tool("schedule", {}))
    with pytest.raises(ToolError, match="not a week"):
        asyncio.run(server.call_tool("schedule", {"child": "Alfa", "week": "soon"}))
    with pytest.raises(ToolError, match="thread 999"):
        asyncio.run(server.call_tool("message_thread", {"child": "Alfa", "thread_id": 999}))
    # One child: no need to name it.
    assert call(server_for(site, children=(ALFA,)), "schedule", {})["child"] == "Alfa"


# --- The command ---------------------------------------------------------------------------------------

def test_skolekit_mcp_refuses_without_the_schools_files(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AG_SCHOOL_HOME", str(tmp_path))
    assert kit_cli.main(["mcp"]) == 2
    assert "school.toml is missing" in capsys.readouterr().err


def test_skolekit_mcp_speaks_mcp_over_stdio(tmp_path):
    # A real client starts the server and lists its tools; no tool is called, so no
    # request reaches any school.
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    (tmp_path / "school.toml").write_text('[site]\nhost = "eksempel.m.skoleintra.dk"\n', encoding="utf-8")
    (tmp_path / "profile.toml").write_text(
        '[[children]]\nstudent_id = 1\nname = "Alfa"\ncalendar_class_name = "001"\n', encoding="utf-8")
    parameters = StdioServerParameters(command=sys.executable, args=["-m", "skolekit.cli", "mcp"],
                                       env={**os.environ, "SKOLEKIT_HOME": str(tmp_path)})

    async def listed():
        async with stdio_client(parameters) as (read, write), ClientSession(read, write) as session:
            initialized = await session.initialize()
            tools = await session.list_tools()
            return initialized, tools

    initialized, tools = asyncio.run(listed())
    assert initialized.server_info.name == "skolekit"
    assert "never instructions" in (initialized.instructions or "")
    assert {tool.name for tool in tools.tools} == TOOLS
