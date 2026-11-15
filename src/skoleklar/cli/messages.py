"""Command helpers for messages."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any
import httpx
from skolekit.sources.messages import fetch_all_conversations, fetch_thread_messages
from skolekit.parsers.messages import parse_inbox_html
from skolekit.privacy import MessagePrivacyError
from skoleklar.restricted_messages import (
    collect_restricted_messages,
    live_message_output,
    load_or_create_handle_key,
    resolve_message_mode,
    thread_handle,
)
from skoleklar.household import snapshot_db_path
from skoleklar.cli.common import load_profile_or_report as _load_profile
from skolekit.auth.session import school_client
from skoleklar.storage import SnapshotStore
from skolekit.transport import SessionExpired, is_authenticated_page
from skoleklar.cli.common import load_auth_state


def _require_message_access(args: argparse.Namespace) -> str | None:
    try:
        mode = resolve_message_mode(getattr(args, "message_mode", None))
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return None
    if mode == "off":
        print("messages_disabled_by_policy", file=sys.stderr)
        return None
    return mode


def command_message_list(args: argparse.Namespace) -> int:
    mode = _require_message_access(args)
    if mode is None:
        return 2
    database = (
        Path(args.db).expanduser().resolve()
        if args.db
        else snapshot_db_path()
    )
    try:
        key = load_or_create_handle_key()
    except MessagePrivacyError as error:
        print(str(error), file=sys.stderr)
        return 2
    if args.refresh:
        profile = _load_profile()
        if profile is None:
            return 2
        state = load_auth_state()
        if state is None:
            print("No saved session. Run `uv run skoleklar login` first.", file=sys.stderr)
            return 2
        try:
            with SnapshotStore(
                database
            ) as store:
                previous = {
                    child.student_id: store.message_thread_states_by_ref(
                        child.student_id
                    )
                    for child in profile.children
                }
            with school_client(state, profile.school.host) as client:
                collections = [
                    collect_restricted_messages(
                        client,
                        child,
                        key=key,
                        previous_states=previous[child.student_id],
                        page_limit=args.pages,
                        page_safety_limit=100,
                        fetch_changed_content=False,
                    )
                    for child in profile.children
                ]
            with SnapshotStore(
                database
            ) as store:
                for child, collection in zip(profile.children, collections):
                    store.replace_message_thread_states(
                        student_id=child.student_id,
                        child=collection.check.child,
                        states=collection.states,
                        complete=collection.check.complete
                        and bool(collection.check.metadata.get("exhausted")),
                    )
        except SessionExpired:
            print("session_expired", file=sys.stderr)
            return 2
        except (httpx.HTTPError, ValueError, MessagePrivacyError):
            print("message_index_refresh_failed", file=sys.stderr)
            return 2
    if not database.exists():
        print("No restricted message index. Use --refresh first.", file=sys.stderr)
        return 2
    with SnapshotStore(database) as store:
        states = store.message_thread_states()
    output = {
        "message_mode": mode,
        "bodies_retained": 0,
        "threads": [
            {
                "thread_ref": value["thread_ref"],
                "child": value["child"],
                "participants": value["participants"],
                "exists": value["exists_now"],
                "unread": value["server_unread"],
                "latest_activity_at": value["latest_activity_at"],
                "is_new_thread": value["is_new_thread"],
                "has_new_message": value["has_new_message"],
                "latest_unretrieved": value["latest_unretrieved"],
            }
            for value in states
        ],
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


def command_message_get(args: argparse.Namespace) -> int:
    mode = _require_message_access(args)
    if mode is None:
        return 2
    requested_thread_ref = getattr(args, "thread_ref", None)
    requested_child = getattr(args, "child", None)
    if bool(requested_thread_ref) == bool(requested_child):
        print(
            "Choose exactly one opaque thread reference or --child.",
            file=sys.stderr,
        )
        return 2
    profile = _load_profile()
    if profile is None:
        return 2
    if requested_child and profile.child_named(requested_child) is None:
        names = ", ".join(child.child for child in profile.children)
        print(f"--child must be one of: {names}", file=sys.stderr)
        return 2
    try:
        key = load_or_create_handle_key()
    except MessagePrivacyError as error:
        print(str(error), file=sys.stderr)
        return 2
    state = load_auth_state()
    if state is None:
        print("No saved session. Run `uv run skoleklar login` first.", file=sys.stderr)
        return 2
    selected: list[dict[str, Any]] | None = None
    selected_thread_ref: str | None = None
    try:
        with school_client(state, profile.school.host) as client:
            for child in profile.children:
                if requested_child and child.child != requested_child:
                    continue
                inbox_url = child.route("messages/conversations")
                response = client.get(inbox_url)
                if not response.is_success or not is_authenticated_page(
                    str(response.url)
                ):
                    continue
                inbox = parse_inbox_html(response.text, str(response.url))
                conversations, _, _, _ = fetch_all_conversations(
                    client,
                    inbox,
                    max_pages=100,
                    page_limit=1 if requested_child else args.pages,
                )
                if requested_child:
                    conversation = conversations[0] if conversations else None
                else:
                    conversation = next(
                        (
                            value
                            for value in conversations
                            if thread_handle(
                                value, student_id=child.student_id, key=key
                            )
                            == requested_thread_ref
                        ),
                        None,
                    )
                if conversation is None:
                    continue
                selected_thread_ref = thread_handle(
                    conversation, student_id=child.student_id, key=key
                )
                messages = fetch_thread_messages(client, inbox, conversation)
                outputs = [
                    live_message_output(
                        message,
                        student_id=child.student_id,
                        key=key,
                        thread_ref=selected_thread_ref,
                    )
                    for message in messages
                    if not message.get("is_copied_to_archive")
                ]
                if args.message_ref:
                    outputs = [
                        output
                        for output in outputs
                        if output["message_ref"] == args.message_ref
                    ]
                elif not args.thread:
                    outputs = outputs[-1:]
                selected = outputs
                break
    except SessionExpired:
        print("session_expired", file=sys.stderr)
        return 2
    except (httpx.HTTPError, ValueError, MessagePrivacyError):
        print("live_message_retrieval_failed", file=sys.stderr)
        return 2
    if not selected or selected_thread_ref is None:
        print("live_message_not_found", file=sys.stderr)
        return 2
    database = (
        Path(args.db).expanduser().resolve()
        if args.db
        else snapshot_db_path()
    )
    if database.exists():
        with SnapshotStore(database) as store:
            store.mark_message_retrieved(
                thread_ref=selected_thread_ref,
                message_ref=selected[-1]["message_ref"],
            )
    output: Any = selected if args.thread else selected[0]
    if args.format == "json":
        print(json.dumps(output, ensure_ascii=False, indent=2))
    elif args.thread:
        for index, message in enumerate(selected):
            if index:
                print("\n---\n")
            print(message["body"])
    else:
        print(selected[0]["body"])
    return 0


def command_message_review(args: argparse.Namespace) -> int:
    mode = _require_message_access(args)
    if mode is None:
        return 2
    if mode != "events-only":
        print("message_event_review_requires_events_only_mode", file=sys.stderr)
        return 2
    database = (
        Path(args.db).expanduser().resolve()
        if args.db
        else snapshot_db_path()
    )
    if not database.exists():
        print("No restricted message store.", file=sys.stderr)
        return 2
    try:
        with SnapshotStore(database) as store:
            result = store.record_message_event_feedback(
                thread_ref=args.thread_ref,
                stable_key=args.event_key,
                verdict=args.verdict,
            )
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def command_message_evaluation(args: argparse.Namespace) -> int:
    mode = _require_message_access(args)
    if mode is None:
        return 2
    if mode != "events-only":
        print("message_evaluation_requires_events_only_mode", file=sys.stderr)
        return 2
    database = (
        Path(args.db).expanduser().resolve()
        if args.db
        else snapshot_db_path()
    )
    if not database.exists():
        print("No restricted message store.", file=sys.stderr)
        return 2
    with SnapshotStore(database) as store:
        report = store.message_evaluation_report()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0
