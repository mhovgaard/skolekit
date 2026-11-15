"""Build the signed `Refresh School` Shortcut for the primary iPhone.

The Shortcut pulls the delivery manifest, writes new and changed items to the
shared `AG School` calendar and the two `School ·` reminder lists, reports
completed reminders and sends a receipt. It never deletes anything.

Each native item carries the marker `[AGSKOLEINTRA:<stable_key>:r<revision>]` in
its notes. A small state file in the Shortcuts folder of iCloud Drive maps every
applied stable key to its revision, so an unchanged item is not touched and a
lost phone resumes without duplicates. A changed item is found by its marker and
edited in place; two matches are reported as `ambiguous_match` and left alone.

The file carries no secret: the gateway key and the device token are import
questions, answered on the phone when the Shortcut is added. The gateway's
hostname is the household's own and is given when the file is built:

    uv run python -m skoleklar.phone_shortcut --host NAME.gateway.dev
"""

from __future__ import annotations

import argparse
import plistlib
import re
import subprocess
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


SHORTCUT_NAME = "Refresh School"
DEVICE_ID = "family-primary-iphone"
STATE_FILE = "ag-school-state.json"
CALENDAR = "AG School"
BRING_LIST = "School · Bring home"
DEADLINE_LIST = "School · Deadlines"
MARKER_PREFIX = "[AGSKOLEINTRA:"
COMPLETED_PATTERN = r"\[AGSKOLEINTRA:([a-z0-9:-]+):r[^\]]*\]"
REMINDER_TIME = "07:00"
# Moving an all-day event: park its end here first so the new start is always
# before the end, whichever way the dates move.
PARKED_END = "2099-12-31"

_GATEWAY_HOST = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+gateway\.dev")

OBJECT = "￼"
_NAMESPACE = uuid.UUID("4f7d9c1e-2b6a-4c55-9e0f-6a7f3a0b8c21")

GATEWAY_QUESTION = (
    "Paste the gateway API key. On the Mac: "
    "uv run python -m skoleklar.device_token gateway-copy"
)
TOKEN_QUESTION = (
    "Paste the device token. On the Mac: "
    "uv run python -m skoleklar.device_token copy"
)

# Shortcuts condition codes.
HAS_VALUE = 100
NO_VALUE = 101
IS = 4
CONTAINS = 99


class Ref:
    """A value inside the Shortcut: an action's output, a variable or the date."""

    def __init__(self, value: dict[str, Any]):
        self.value = value

    def prop(self, name: str) -> "Ref":
        return Ref({**self.value, "Aggrandizements": [
            {"Type": "WFPropertyVariableAggrandizement", "PropertyName": name}]})


def var(name: str) -> Ref:
    return Ref({"Type": "Variable", "VariableName": name})


CURRENT_DATE = Ref({"Type": "CurrentDate"})
REPEAT_ITEM = var("Repeat Item")


def text(*parts: str | Ref) -> dict[str, Any] | str:
    """A text field; plain when it holds no variables."""
    if all(isinstance(part, str) for part in parts):
        return "".join(parts)  # type: ignore[arg-type]
    string = ""
    attachments: dict[str, Any] = {}
    for part in parts:
        if isinstance(part, Ref):
            # Ranges count UTF-16 code units.
            attachments[f"{{{len(string.encode('utf-16-le')) // 2}, 1}}"] = part.value
            string += OBJECT
        else:
            string += part
    return {"Value": {"string": string, "attachmentsByRange": attachments},
            "WFSerializationType": "WFTextTokenString"}


def token(*parts: str | Ref) -> dict[str, Any]:
    """A text field that is always serialized as a token string."""
    value = text(*parts)
    if isinstance(value, str):
        return {"Value": {"string": value, "attachmentsByRange": {}},
                "WFSerializationType": "WFTextTokenString"}
    return value


def attach(ref: Ref) -> dict[str, Any]:
    return {"Value": ref.value, "WFSerializationType": "WFTextTokenAttachment"}


def fields(*items: tuple[str, str | Ref | tuple[str | Ref, ...]]) -> dict[str, Any]:
    """A dictionary field such as the request headers."""
    rows = []
    for key, value in items:
        parts = value if isinstance(value, tuple) else (value,)
        rows.append({"WFItemType": 0, "WFKey": token(key), "WFValue": token(*parts)})
    return {"Value": {"WFDictionaryFieldValueItems": rows},
            "WFSerializationType": "WFDictionaryFieldValue"}


def filters(*templates: dict[str, Any]) -> dict[str, Any]:
    """A Find filter in which every condition must hold."""
    return {"Value": {"WFActionParameterFilterPrefix": 1,
                      "WFContentPredicateBoundedDate": False,
                      "WFActionParameterFilterTemplates": list(templates)},
            "WFSerializationType": "WFContentPredicateTableTemplate"}


def notes_contain(*parts: str | Ref) -> dict[str, Any]:
    return {"Operator": CONTAINS, "Property": "Notes", "Removable": True,
            "Values": {"Unit": 4, "String": text(*parts)}}


class Builder:
    def __init__(self) -> None:
        self.actions: list[dict[str, Any]] = []
        self.questions: list[dict[str, Any]] = []
        self._count = 0

    def _uuid(self) -> str:
        self._count += 1
        return str(uuid.uuid5(_NAMESPACE, f"{SHORTCUT_NAME}:{self._count}")).upper()

    def add(self, identifier: str, output: str | None = None, **parameters: Any) -> Ref | None:
        action_id = self._uuid()
        self.actions.append({
            "WFWorkflowActionIdentifier": f"is.workflow.actions.{identifier}",
            "WFWorkflowActionParameters": {"UUID": action_id, **parameters},
        })
        if output is None:
            return None
        return Ref({"Type": "ActionOutput", "OutputUUID": action_id, "OutputName": output})

    # Plain actions -----------------------------------------------------------

    def comment(self, value: str) -> None:
        self.add("comment", WFCommentActionText=value)

    def text(self, *parts: str | Ref, question: str | None = None) -> Ref:
        if question is not None:
            self.questions.append({"ActionIndex": len(self.actions), "Category": "Parameter",
                                   "DefaultValue": "", "ParameterKey": "WFTextActionText",
                                   "Text": question})
        return self.add("gettext", "Text", WFTextActionText=text(*parts))  # type: ignore[return-value]

    def set(self, name: str, value: Ref) -> Ref:
        self.add("setvariable", WFVariableName=name, WFInput=attach(value))
        return var(name)

    def append(self, name: str, value: Ref) -> None:
        self.add("appendvariable", WFVariableName=name, WFInput=attach(value))

    def value(self, source: Ref, *key: str | Ref) -> Ref:
        return self.add("getvalueforkey", "Dictionary Value", WFGetDictionaryValueType="Value",
                        WFDictionaryKey=text(*key), WFInput=attach(source))  # type: ignore[return-value]

    def keys(self, source: Ref) -> Ref:
        return self.add("getvalueforkey", "Dictionary Value", WFGetDictionaryValueType="All Keys",
                        WFInput=attach(source))  # type: ignore[return-value]

    def put(self, source: Ref, key: Ref, value: Ref) -> Ref:
        return self.add("setvalueforkey", "Dictionary", WFDictionary=attach(source),
                        WFDictionaryKey=text(key), WFDictionaryValue=text(value))  # type: ignore[return-value]

    def empty_dictionary(self) -> Ref:
        return self.add("dictionary", "Dictionary", WFItems={
            "Value": {"WFDictionaryFieldValueItems": []},
            "WFSerializationType": "WFDictionaryFieldValue"})  # type: ignore[return-value]

    def count(self, source: Ref) -> Ref:
        return self.add("count", "Count", WFCountType="Items", Input=attach(source))  # type: ignore[return-value]

    def join(self, source: Ref, separator: str) -> Ref:
        return self.add("text.combine", "Combined Text", WFTextSeparator="Custom",
                        WFTextCustomSeparator=separator, text=attach(source))  # type: ignore[return-value]

    def now(self) -> Ref:
        return self.add("format.date", "Formatted Date", WFDateFormatStyle="ISO 8601",
                        WFISO8601IncludeTime=True, WFDate=text(CURRENT_DATE))  # type: ignore[return-value]

    def post(self, path: str, body: Ref) -> Ref:
        return self.add(  # type: ignore[return-value]
            "downloadurl", "Contents of URL",
            WFURL=text("https://", var("Host"), path),
            WFHTTPMethod="POST",
            WFHTTPHeaders=fields(("x-api-key", var("Gateway key")),
                                 ("Authorization", ("Bearer ", var("Device token"))),
                                 ("Content-Type", "application/json")),
            WFHTTPBodyType="File",
            WFRequestVariable=attach(body),
            ShowHeaders=True,
        )

    def notify(self, title: str, *body: str | Ref) -> None:
        self.add("notification", WFNotificationActionTitle=title,
                 WFNotificationActionBody=text(*body), WFNotificationActionSound=True)

    def save_state(self, state: Ref) -> None:
        self.add("documentpicker.save", WFInput=attach(state), WFAskWhereToSave=False,
                 WFFileDestinationPath=STATE_FILE, WFSaveFileOverwrite=True)

    # Control flow -------------------------------------------------------------

    @contextmanager
    def when(self, subject: Ref, condition: int, *compare: str | Ref,
             number: str | None = None) -> Iterator["_Branch"]:
        group = self._uuid()
        parameters: dict[str, Any] = {
            "WFInput": {"Type": "Variable", "Variable": attach(subject)},
            "WFCondition": condition, "GroupingIdentifier": group, "WFControlFlowMode": 0}
        if compare:
            parameters["WFConditionalActionString"] = text(*compare)
        if number is not None:
            parameters["WFNumberValue"] = number
        self.add("conditional", **parameters)
        yield _Branch(self, group)
        self.add("conditional", GroupingIdentifier=group, WFControlFlowMode=2)

    @contextmanager
    def each(self, source: Ref) -> Iterator[None]:
        group = self._uuid()
        self.add("repeat.each", WFInput=attach(source), GroupingIdentifier=group,
                 WFControlFlowMode=0)
        yield
        self.add("repeat.each", GroupingIdentifier=group, WFControlFlowMode=2)

    def workflow(self) -> dict[str, Any]:
        return {
            "WFWorkflowActions": self.actions,
            "WFWorkflowClientVersion": "4018.0.4",
            "WFWorkflowMinimumClientVersion": 900,
            "WFWorkflowMinimumClientVersionString": "900",
            "WFWorkflowIcon": {"WFWorkflowIconStartColor": 463140863,
                               "WFWorkflowIconGlyphNumber": 59446},
            "WFWorkflowImportQuestions": self.questions,
            "WFWorkflowTypes": [],
            "WFWorkflowInputContentItemClasses": [],
            "WFWorkflowOutputContentItemClasses": [],
            "WFQuickActionSurfaces": [],
            "WFWorkflowHasShortcutInputVariables": False,
            "WFWorkflowHasOutputFallback": False,
        }


class _Branch:
    def __init__(self, builder: Builder, group: str):
        self.builder = builder
        self.group = group

    def otherwise(self) -> None:
        self.builder.add("conditional", GroupingIdentifier=self.group, WFControlFlowMode=1)


def _json_list(b: Builder, source: str, separator: str, quote: str, target: str) -> Ref:
    """Set `target` to a JSON array text built from the list variable `source`."""
    with b.when(var(source), HAS_VALUE) as branch:
        joined = b.join(var(source), separator)
        b.set(target, b.text(f"[{quote}", joined, f"{quote}]"))
        branch.otherwise()
        b.set(target, b.text("[]"))
    return var(target)


def _item_fields(b: Builder, names: tuple[str, ...]) -> dict[str, Ref]:
    item = b.set("Item", REPEAT_ITEM)
    values = {name: b.set(f"Item {name}", b.value(item, name)) for name in names}
    b.set("Outcome", b.text("ok"))
    b.set("Key", values["stable_key"])
    b.set("Revision", b.text("r", values["revision"]))
    b.set("Marker", b.text(MARKER_PREFIX, var("Key"), ":", var("Revision"), "]"))
    with b.when(values["source_url"], CONTAINS, "http") as branch:
        b.set("Notes", b.text(values["notes"], "\n\n", values["source_url"], "\n", var("Marker")))
        branch.otherwise()
        b.set("Notes", b.text(values["notes"], "\n\n", var("Marker")))
    return values


def _remember(b: Builder) -> None:
    """Save a written item's revision at once, so a stopped run never repeats it."""
    with b.when(var("Outcome"), IS, "ok"):
        b.set("State", b.put(var("State"), var("Key"), var("Revision")))
        b.save_state(var("State"))
        b.append("Changed", var("Key"))


def _reconcile(b: Builder, section: str, write: Any) -> None:
    names = ("stable_key", "revision", "title", "notes", "source_url") + (
        ("start", "end") if section == "calendar" else ("list", "due"))
    with b.each(b.value(var("Manifest"), section)):
        values = _item_fields(b, names)
        applied = b.value(var("State"), var("Key"))
        with b.when(applied, IS, var("Revision")) as unchanged:
            unchanged.otherwise()
            write(b, values)
            _remember(b)
        with b.when(var("Outcome"), IS, "ok") as branch:
            b.append("Applied", var("Key"))
            branch.otherwise()
            b.append("Failed", b.text('{"stable_key":"', var("Key"), '","code":"ambiguous_match"}'))


def _write_event(b: Builder, values: dict[str, Ref]) -> None:
    found = b.set("Found", b.add("filter.calendarevents", "Calendar Events",
                                 WFContentItemFilter=filters(notes_contain(MARKER_PREFIX, var("Key"), ":r")),
                                 WFContentItemLimitEnabled=True, WFContentItemLimitNumber=5.0))
    matches = b.count(found)
    with b.when(matches, IS, number="0") as none:
        b.add("addnewevent", WFCalendarItemTitle=text(values["title"]),
              WFCalendarItemStartDate=text(values["start"]), WFCalendarItemEndDate=text(values["end"]),
              WFCalendarItemAllDay=True, WFCalendarItemNotes=text(var("Notes")),
              WFCalendarItemCalendar=CALENDAR,
              WFCalendarDescriptor={"Title": CALENDAR, "IsAllCalendar": False},
              ShowWhenRun=False)
        none.otherwise()
        with b.when(matches, IS, number="1") as one:
            for name, value in (("Title", values["title"]), ("End Date", PARKED_END),
                                ("Start Date", values["start"]), ("End Date", values["end"]),
                                ("Notes", var("Notes"))):
                b.add("setters.calendarevents", WFInput=attach(found),
                      WFContentItemPropertyName=name,
                      **{"WFCalendarEventContentItem" + name.replace(" ", ""): text(value)})
            one.otherwise()
            b.set("Outcome", b.text("ambiguous"))


def _write_reminder(b: Builder, values: dict[str, Ref]) -> None:
    due = b.set("Due", b.text(values["due"], f" {REMINDER_TIME}"))
    found = b.set("Found", b.add("filter.reminders", "Reminders",
                                 WFContentItemFilter=filters(notes_contain(MARKER_PREFIX, var("Key"), ":r")),
                                 WFContentItemLimitEnabled=True, WFContentItemLimitNumber=5.0))
    matches = b.count(found)
    with b.when(matches, IS, number="0") as none:
        with b.when(values["list"], IS, BRING_LIST) as bring:
            _add_reminder(b, values, due, BRING_LIST)
            bring.otherwise()
            _add_reminder(b, values, due, DEADLINE_LIST)
        none.otherwise()
        with b.when(matches, IS, number="1") as one:
            # Completion belongs to the family: an edit never reopens a reminder.
            for name, value in (("Title", values["title"]), ("Notes", var("Notes")),
                                ("Due Date", due)):
                b.add("setters.reminders", WFInput=attach(found),
                      WFContentItemPropertyName=name,
                      **{"WFReminderContentItem" + name.replace(" ", ""): text(value)})
            one.otherwise()
            b.set("Outcome", b.text("ambiguous"))


def _add_reminder(b: Builder, values: dict[str, Ref], due: Ref, list_name: str) -> None:
    b.add("addnewreminder", WFCalendarItemTitle=text(values["title"]),
          WFCalendarItemNotes=text(var("Notes")), WFCalendarItemCalendar=list_name,
          WFCalendarDescriptor={"Title": list_name, "IsAllCalendar": False},
          WFAlertEnabled="Alert", WFAlertCondition="At Time", WFAlertCustomTime=text(due),
          ShowWhenRun=False)


def gateway_host(text: str) -> str:
    """The household's API Gateway hostname, such as `NAME.ew.gateway.dev`: no scheme, port or path."""
    host = str(text).strip().lower()
    if not _GATEWAY_HOST.fullmatch(host):
        raise ValueError("Give the gateway's hostname, such as NAME.ew.gateway.dev, without https:// or a path")
    return host


def build_workflow(host: str) -> dict[str, Any]:
    host = gateway_host(host)
    b = Builder()
    b.comment(
        "Refresh School · Skoleklar\n"
        "Pulls the school manifest and writes new and changed items to the AG School "
        "calendar and the School · Bring home and School · Deadlines lists. "
        "Completed reminders are reported back. Nothing is ever deleted.\n"
        f"State: {STATE_FILE} in the Shortcuts folder of iCloud Drive."
    )
    b.set("Host", b.text(host))
    b.set("Gateway key", b.text("", question=GATEWAY_QUESTION))
    b.set("Device token", b.text("", question=TOKEN_QUESTION))

    b.comment("Which items this phone has already written, by stable key and revision.")
    stored = b.add("documentpicker.open", "File", WFGetFilePath=STATE_FILE,
                   WFFileErrorIfNotFound=False, WFShowFilePicker=False)
    with b.when(stored, HAS_VALUE) as branch:  # type: ignore[arg-type]
        b.set("State", b.add("detect.dictionary", "Dictionary", WFInput=attach(stored)))  # type: ignore[arg-type]
        branch.otherwise()
        b.set("State", b.empty_dictionary())

    b.comment("Completed school reminders go back to the cloud, each key once.")
    b.set("Completed set", b.empty_dictionary())
    done = b.add("filter.reminders", "Reminders", WFContentItemFilter=filters(
        {"Operator": IS, "Property": "Is Completed", "Removable": True, "Values": {"Bool": True}},
        notes_contain(MARKER_PREFIX)), WFContentItemLimitEnabled=True, WFContentItemLimitNumber=100.0)
    with b.each(done):  # type: ignore[arg-type]
        found = b.add("text.match", "Matches", WFMatchTextPattern=COMPLETED_PATTERN,
                      WFMatchTextCaseSensitive=True, text=text(REPEAT_ITEM.prop("Notes")))
        key = b.add("text.match.getgroup", "Text", WFGetGroupType="Group At Index",
                    WFGroupIndex=1.0, matches=attach(found))  # type: ignore[arg-type]
        with b.when(key, HAS_VALUE):  # type: ignore[arg-type]
            b.set("Completed set", b.put(var("Completed set"), key, b.text("1")))  # type: ignore[arg-type]
    b.set("Completed", b.keys(var("Completed set")))
    completed = _json_list(b, "Completed", '","', '"', "Completed JSON")

    b.comment("Ask for the current manifest.")
    request = b.text('{"schema_version":1,"device_id":"', DEVICE_ID,
                     '","last_manifest_id":null,"completed_stable_keys":', completed,
                     ',"observed_at":"', b.now(), '"}')
    b.set("Manifest", b.post("/v1/sync", request))
    manifest_id = b.set("Manifest ID", b.value(var("Manifest"), "manifest_id"))
    with b.when(manifest_id, NO_VALUE):
        error = b.value(var("Manifest"), "error")
        b.notify("Skole", "Skolen kunne ikke hentes (", error, "). Prøv igen senere.")
        b.add("exit")

    b.comment(f"Calendar entries go to {CALENDAR}.")
    _reconcile(b, "calendar", _write_event)
    b.comment(f"Reminders go to {BRING_LIST} or {DEADLINE_LIST}.")
    _reconcile(b, "reminders", _write_reminder)

    b.comment("Tell the cloud what reached this phone.")
    applied = _json_list(b, "Applied", '","', '"', "Applied JSON")
    failed = _json_list(b, "Failed", ",", "", "Failed JSON")
    receipt = b.text('{"schema_version":1,"device_id":"', DEVICE_ID, '","manifest_id":"',
                     manifest_id, '","applied_stable_keys":', applied, ',"failed":', failed,
                     ',"finished_at":"', b.now(), '"}')
    b.post("/v1/receipts", receipt)
    with b.when(var("Changed"), HAS_VALUE):
        b.notify("Skole", b.count(var("Changed")), " nye eller ændrede punkter i kalender og påmindelser.")
    with b.when(var("Failed"), HAS_VALUE):
        b.notify("Skole", b.count(var("Failed")), " punkter findes to gange og blev ikke ændret.")
    return b.workflow()


def write_shortcut(output: Path, *, host: str, sign: bool = True) -> Path:
    data = plistlib.dumps(build_workflow(host), fmt=plistlib.FMT_BINARY)
    output.parent.mkdir(parents=True, exist_ok=True)
    if not sign:
        output.write_bytes(data)
        return output
    unsigned = output.with_name(output.stem + ".unsigned.shortcut")
    unsigned.write_bytes(data)
    try:
        subprocess.run(["shortcuts", "sign", "--mode", "anyone", "--input", str(unsigned),
                        "--output", str(output)], check=True)
    finally:
        unsigned.unlink(missing_ok=True)
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the Refresh School iPhone Shortcut")
    parser.add_argument("--host", required=True,
                        help="the gateway's hostname, as deploy-gateway.sh prints it (NAME.ew.gateway.dev)")
    parser.add_argument("--output", type=Path, default=Path("dist") / f"{SHORTCUT_NAME}.shortcut")
    parser.add_argument("--unsigned", action="store_true",
                        help="write the plain plist; iOS will not import it")
    args = parser.parse_args(argv)
    try:
        host = gateway_host(args.host)
    except ValueError as error:
        parser.error(str(error))
    path = write_shortcut(args.output, host=host, sign=not args.unsigned)
    print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
