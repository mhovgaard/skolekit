import json
import plistlib
import re
from typing import Any, Callable, Iterator

import pytest

from skoleklar.contracts import validate_contract
from skoleklar.phone_shortcut import (
    BRING_LIST,
    CALENDAR,
    COMPLETED_PATTERN,
    DEADLINE_LIST,
    OBJECT,
    build_workflow,
    gateway_host,
    main,
    write_shortcut,
)

HOST = "example.ew.gateway.dev"


def actions() -> list[dict[str, Any]]:
    return build_workflow(HOST)["WFWorkflowActions"]


def kind(action: dict[str, Any]) -> str:
    return action["WFWorkflowActionIdentifier"].removeprefix("is.workflow.actions.")


def walk(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def render(field: Any, resolve: Callable[[dict[str, Any]], str]) -> str:
    """Fill a text field the way Shortcuts does, with test values."""
    if isinstance(field, str):
        return field
    value = field["Value"]
    units = value["string"].encode("utf-16-le")
    out, last = [], 0
    for position, attachment in sorted(
        ((int(re.match(r"\{(\d+), 1\}", key).group(1)), attachment)
         for key, attachment in value["attachmentsByRange"].items()),
        key=lambda pair: pair[0],
    ):
        out.append(units[last * 2:position * 2].decode("utf-16-le"))
        out.append(resolve(attachment))
        last = position + 1
    out.append(units[last * 2:].decode("utf-16-le"))
    text = "".join(out)
    assert OBJECT not in text
    return text


def text_action(start: str) -> dict[str, Any]:
    for action in actions():
        field = action["WFWorkflowActionParameters"].get("WFTextActionText")
        if kind(action) == "gettext" and render(field, lambda _: "").startswith(start):
            return action
    raise AssertionError(f"no text action starts with {start!r}")


def test_control_flow_is_balanced() -> None:
    stack: list[tuple[str, str]] = []
    for action in actions():
        parameters = action["WFWorkflowActionParameters"]
        if kind(action) not in {"conditional", "repeat.each"}:
            continue
        group, mode = parameters["GroupingIdentifier"], parameters["WFControlFlowMode"]
        if mode == 0:
            stack.append((kind(action), group))
        elif mode == 1:
            assert stack[-1] == ("conditional", group)
        else:
            assert stack.pop() == (kind(action), group)
    assert stack == []


def test_outputs_and_variables_are_defined_before_use() -> None:
    seen_outputs: set[str] = set()
    variables = {"Repeat Item"}
    for action in actions():
        parameters = action["WFWorkflowActionParameters"]
        for value in walk(parameters):
            if value.get("Type") == "ActionOutput":
                assert value["OutputUUID"] in seen_outputs, value
            if value.get("Type") == "Variable" and "VariableName" in value:
                assert value["VariableName"] in variables, value
        if kind(action) in {"setvariable", "appendvariable"}:
            variables.add(parameters["WFVariableName"])
        seen_outputs.add(parameters["UUID"])


def test_secrets_are_import_questions_and_never_in_the_file() -> None:
    workflow = build_workflow(HOST)
    questions = workflow["WFWorkflowImportQuestions"]
    assert len(questions) == 2
    for question in questions:
        action = workflow["WFWorkflowActions"][question["ActionIndex"]]
        assert kind(action) == "gettext"
        assert action["WFWorkflowActionParameters"][question["ParameterKey"]] == ""
    headers = [action["WFWorkflowActionParameters"]["WFHTTPHeaders"]
               for action in workflow["WFWorkflowActions"] if kind(action) == "downloadurl"]
    assert len(headers) == 2
    for header in headers:
        names = {row["WFKey"]["Value"]["string"]
                 for row in header["Value"]["WFDictionaryFieldValueItems"]}
        assert names == {"x-api-key", "Authorization", "Content-Type"}


def test_destinations_are_the_shared_calendar_and_lists() -> None:
    written = {(kind(action), action["WFWorkflowActionParameters"]["WFCalendarItemCalendar"])
               for action in actions() if kind(action) in {"addnewevent", "addnewreminder"}}
    assert written == {("addnewevent", CALENDAR), ("addnewreminder", BRING_LIST),
                       ("addnewreminder", DEADLINE_LIST)}
    assert not any(kind(action).startswith("remove") or "delete" in kind(action)
                   for action in actions())


def _values(attachment: dict[str, Any], variables: dict[str, str]) -> str:
    if attachment.get("Type") == "Variable":
        return variables[attachment["VariableName"]]
    if attachment.get("OutputName") == "Formatted Date":
        return "2026-11-07T20:00:07+01:00"
    raise AssertionError(attachment)


def test_sync_request_matches_the_contract() -> None:
    field = text_action('{"schema_version":1,"device_id":"family-primary-iphone","last_manifest_id"')
    for completed in ("[]", '["message-event:abc","0a23"]'):
        body = render(field["WFWorkflowActionParameters"]["WFTextActionText"],
                      lambda attachment: _values(attachment, {"Completed JSON": completed}))
        validate_contract("sync-request-v1", json.loads(body))


def test_receipt_matches_the_contract() -> None:
    failure = render(text_action('{"stable_key":"')["WFWorkflowActionParameters"]["WFTextActionText"],
                     lambda attachment: _values(attachment, {"Key": "0a23"}))
    field = text_action('{"schema_version":1,"device_id":"family-primary-iphone","manifest_id"')
    for applied, failed in (("[]", "[]"), ('["message-event:abc"]', f"[{failure},{failure}]")):
        variables = {"Applied JSON": applied, "Failed JSON": failed,
                     "Manifest ID": "20261107T131912Z-731e61b09d1f"}
        body = render(field["WFWorkflowActionParameters"]["WFTextActionText"],
                      lambda attachment: _values(attachment, variables))
        validate_contract("receipt-v1", json.loads(body))


def test_marker_round_trips_through_the_completion_pattern() -> None:
    field = text_action("[AGSKOLEINTRA:")["WFWorkflowActionParameters"]["WFTextActionText"]
    for key in ("message-event:bb50176", "0a2381b2f27ae234c201b1e2d6640b164152090d"):
        for revision in ("r667702229", "r2.039.724.705"):
            marker = render(field, lambda attachment: {"Key": key, "Revision": revision}[
                attachment["VariableName"]])
            notes = f"Skal med man. 9. nov.\n\nhttps://x.skoleintra.dk/1\n{marker}"
            assert re.search(COMPLETED_PATTERN, notes).group(1) == key


def test_unsigned_file_is_a_binary_plist(tmp_path) -> None:
    path = write_shortcut(tmp_path / "Refresh School.shortcut", host=HOST, sign=False)
    assert plistlib.loads(path.read_bytes()) == build_workflow(HOST)


def test_the_gateway_host_is_the_one_given() -> None:
    hosts = [render(action["WFWorkflowActionParameters"]["WFTextActionText"], lambda _: "")
             for action in actions() if kind(action) == "gettext"]
    assert HOST in hosts
    assert not [text for text in hosts if text.endswith(".gateway.dev") and text != HOST]


@pytest.mark.parametrize("text", ["https://example.ew.gateway.dev", "example.ew.gateway.dev/v1",
                                  "example.gateway.dev:443", "example.com", "", "gateway.dev"])
def test_a_host_that_is_not_a_bare_gateway_hostname_is_refused(text) -> None:
    with pytest.raises(ValueError, match="gateway's hostname"):
        gateway_host(text)


def test_the_command_needs_a_host_and_writes_it(tmp_path, capsys) -> None:
    with pytest.raises(SystemExit):
        main(["--unsigned", "--output", str(tmp_path / "none.shortcut")])
    with pytest.raises(SystemExit):
        main(["--host", "https://example.ew.gateway.dev", "--unsigned", "--output", str(tmp_path / "bad.shortcut")])
    assert not (tmp_path / "bad.shortcut").exists()
    output = tmp_path / "Refresh School.shortcut"
    assert main(["--host", " Example.EW.gateway.dev ", "--unsigned", "--output", str(output)]) == 0
    assert plistlib.loads(output.read_bytes()) == build_workflow(HOST)
