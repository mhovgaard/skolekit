"""The household's restricted message collection: modes, opaque handles, the index.

Restricted collection persists only minimized current inbox metadata and
passes changed-message text to extraction in memory. Message text never
becomes a source record. See docs/message-harvesting.md.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx

from skolekit.parsers.messages import parse_inbox_html
from skolekit.privacy import (
    MessagePrivacyError,
    conversation_source_id,
    normalize_latest_activity,
    shorten_name,
    shorten_participants,
)
from skolekit.records import SourceCheck
from skolekit.sources.messages import fetch_all_conversations, fetch_thread_messages
from skoleklar.settings import KEYCHAIN_SERVICE


MESSAGE_MODES = {"off", "events-only"}
# Removed on 14 November 2026: it stored message text as records.
REMOVED_MESSAGE_MODES = {"legacy"}
MESSAGE_PROCESSORS = {"disabled", "local", "zdr"}
MESSAGE_POLICY_VERSION = "restricted-messages-v5"
MESSAGE_MODE_ENV = "AG_SCHOOL_MESSAGE_MODE"
MESSAGE_PROCESSOR_ENV = "AG_SCHOOL_MESSAGE_PROCESSOR"
MESSAGE_HANDLE_KEY_ENV = "AG_SCHOOL_MESSAGE_HANDLE_KEY"
MESSAGE_HANDLE_KEYCHAIN_ACCOUNT = "message-handle-key"


def resolve_message_mode(explicit: str | None = None) -> str:
    value = (explicit or os.environ.get(MESSAGE_MODE_ENV) or "off").strip().lower()
    if value in REMOVED_MESSAGE_MODES:
        raise ValueError(
            f"The {value} message mode was removed: message text is never stored. "
            "Choose off or events-only."
        )
    if value not in MESSAGE_MODES:
        raise ValueError(f"Unsupported message mode {value!r}; choose off or events-only")
    return value


def resolve_message_processor(explicit: str | None = None) -> str:
    value = (
        explicit or os.environ.get(MESSAGE_PROCESSOR_ENV) or "disabled"
    ).strip().lower()
    if value not in MESSAGE_PROCESSORS:
        raise ValueError(
            f"Unsupported message processor {value!r}; choose disabled, local, or zdr"
        )
    return value


def load_or_create_handle_key() -> bytes:
    configured = os.environ.get(MESSAGE_HANDLE_KEY_ENV)
    if configured:
        return configured.encode("utf-8")
    try:
        import keyring

        value = keyring.get_password(
            KEYCHAIN_SERVICE, MESSAGE_HANDLE_KEYCHAIN_ACCOUNT
        )
        if not value:
            value = secrets.token_urlsafe(32)
            keyring.set_password(
                KEYCHAIN_SERVICE, MESSAGE_HANDLE_KEYCHAIN_ACCOUNT, value
            )
        return value.encode("utf-8")
    except Exception as error:  # pragma: no cover - backend-specific
        raise MessagePrivacyError(
            "A message handle key is required; set AG_SCHOOL_MESSAGE_HANDLE_KEY "
            "or configure a working system keyring"
        ) from error


def opaque_handle(
    key: bytes, *, namespace: str, student_id: int, source_id: Any
) -> str:
    if not key:
        raise ValueError("Message handle key cannot be empty")
    material = f"{namespace}\0{student_id}\0{source_id}".encode("utf-8")
    digest = hmac.new(key, material, hashlib.sha256).digest()[:10]
    token = base64.b32encode(digest).decode("ascii").rstrip("=").lower()
    prefix = "thr" if namespace.startswith("thread") else "msg"
    return f"{prefix}_{token}"


def thread_handle(
    conversation: dict[str, Any], *, student_id: int, key: bytes
) -> str:
    namespace, source_id = conversation_source_id(conversation)
    return opaque_handle(
        key,
        namespace="thread" if namespace == "thread" else "threadless",
        student_id=student_id,
        source_id=source_id,
    )


def message_handle(message: dict[str, Any], *, student_id: int, key: bytes) -> str:
    source_id = message.get("message_id")
    if source_id is None:
        source_id = message.get("source_hash")
    if source_id is None:
        raise MessagePrivacyError("Message has no resolvable identifier")
    return opaque_handle(
        key,
        namespace="message",
        student_id=student_id,
        source_id=source_id,
    )


def safe_thread_state(
    conversation: dict[str, Any],
    *,
    student_id: int,
    child: str,
    key: bytes,
    previous: dict[str, Any] | None = None,
    inbox_position: int = 0,
    observed_on: date | None = None,
) -> dict[str, Any]:
    if conversation.get("latest_message_id") is None:
        raise MessagePrivacyError("Conversation has no latest message identifier")
    current_message_ref = opaque_handle(
        key,
        namespace="message",
        student_id=student_id,
        source_id=conversation["latest_message_id"],
    )
    latest_inbox_message_id = conversation.get("latest_inbox_message_id")
    if "latest_inbox_message_id" not in conversation:
        latest_inbox_message_id = conversation["latest_message_id"]
    latest_inbox_message_ref = (
        opaque_handle(
            key,
            namespace="message",
            student_id=student_id,
            source_id=latest_inbox_message_id,
        )
        if latest_inbox_message_id is not None
        else None
    )
    last_retrieved_ref = (previous or {}).get("last_retrieved_message_ref")
    previous_message_ref = (previous or {}).get("latest_message_ref")
    is_new_thread = previous is None
    return {
        "thread_ref": thread_handle(conversation, student_id=student_id, key=key),
        "student_id": student_id,
        "child": shorten_name(child),
        "participants": shorten_participants(conversation.get("participants_text")),
        "exists_now": True,
        "server_unread": bool(conversation.get("is_unread")),
        "is_new_thread": is_new_thread,
        "has_new_message": (
            not is_new_thread and previous_message_ref != current_message_ref
        ),
        "latest_unretrieved": last_retrieved_ref != current_message_ref,
        "latest_activity_at": normalize_latest_activity(
            conversation.get("date_text"), observed_on=observed_on
        ),
        "inbox_position": inbox_position,
        "latest_message_ref": current_message_ref,
        "latest_inbox_message_ref": latest_inbox_message_ref,
        "last_retrieved_message_ref": last_retrieved_ref,
        "policy_version": MESSAGE_POLICY_VERSION,
    }


@dataclass(slots=True, repr=False)
class EphemeralThread:
    student_id: int
    child: str
    thread_ref: str
    latest_message_ref: str
    conversation: dict[str, Any]
    messages: list[dict[str, Any]]

    def __repr__(self) -> str:
        return (
            "EphemeralThread("
            f"student_id={self.student_id!r}, child={self.child!r}, "
            f"thread_ref={self.thread_ref!r}, message_count={len(self.messages)})"
        )

    def __reduce__(self) -> Any:
        raise TypeError("EphemeralThread cannot be serialized")


@dataclass(slots=True)
class RestrictedMessageCollection:
    check: SourceCheck
    states: list[dict[str, Any]]
    changed_threads: list[EphemeralThread]


def collect_restricted_messages(
    client: httpx.Client,
    child: Any,
    *,
    key: bytes,
    previous_states: dict[str, dict[str, Any]],
    page_limit: int | None,
    page_safety_limit: int,
    fetch_changed_content: bool,
) -> RestrictedMessageCollection:
    inbox_url = child.route("messages/conversations")
    response = client.get(inbox_url)
    response.raise_for_status()
    inbox = parse_inbox_html(response.text, str(response.url))
    conversations, scope_complete, exhausted, pages = fetch_all_conversations(
        client,
        inbox,
        max_pages=page_safety_limit,
        page_limit=page_limit,
    )
    child_display = shorten_name(child.child)
    states: list[dict[str, Any]] = []
    changed_threads: list[EphemeralThread] = []
    failures = 0
    for inbox_position, conversation in enumerate(conversations):
        reference = thread_handle(
            conversation, student_id=child.student_id, key=key
        )
        previous = previous_states.get(reference)
        state = safe_thread_state(
            conversation,
            student_id=child.student_id,
            child=child_display,
            key=key,
            previous=previous,
            inbox_position=inbox_position,
        )
        states.append(state)
        previous_inbox_ref = (previous or {}).get("latest_inbox_message_ref")
        current_inbox_ref = state["latest_inbox_message_ref"]
        already_processed = bool((previous or {}).get("latest_message_processed"))
        if (
            not fetch_changed_content
            or (
                current_inbox_ref == previous_inbox_ref
                and already_processed
            )
        ):
            continue
        try:
            messages = fetch_thread_messages(client, inbox, conversation)
        except (httpx.HTTPError, ValueError):
            failures += 1
            continue
        eligible = [
            message
            for message in messages
            if not message.get("is_outbox")
            and not message.get("is_copied_to_archive")
        ]
        if not eligible:
            state["latest_inbox_message_ref"] = None
            continue
        fetched_inbox_ref = message_handle(
            eligible[-1], student_id=child.student_id, key=key
        )
        state["latest_inbox_message_ref"] = fetched_inbox_ref
        if fetched_inbox_ref == previous_inbox_ref and already_processed:
            continue
        changed_threads.append(
            EphemeralThread(
                student_id=child.student_id,
                child=child_display,
                thread_ref=reference,
                latest_message_ref=fetched_inbox_ref,
                conversation=conversation,
                messages=messages,
            )
        )
    check = SourceCheck(
        student_id=child.student_id,
        child=child_display,
        source_type="messages",
        records=[],
        complete=scope_complete and failures == 0,
        error=(
            None
            if failures == 0
            else f"MessagePrivacyError: {failures} changed thread(s) could not be read"
        ),
        metadata={
            "message_mode": "events-only",
            "conversation_count": len(states),
            "unread_conversation_count": sum(
                1 for state in states if state["server_unread"]
            ),
            "new_thread_count": sum(
                1 for state in states if state["is_new_thread"]
            ),
            "new_message_in_existing_thread_count": sum(
                1 for state in states if state["has_new_message"]
            ),
            "latest_unretrieved_count": sum(
                1 for state in states if state["latest_unretrieved"]
            ),
            "pages": pages,
            "exhausted": exhausted,
            "bodies_retained": 0,
        },
        coverage={"kind": "current_state", "pages": pages},
    )
    return RestrictedMessageCollection(
        check=check, states=states, changed_threads=changed_threads
    )


def live_message_output(
    message: dict[str, Any],
    *,
    student_id: int,
    key: bytes,
    thread_ref: str,
) -> dict[str, Any]:
    return {
        "thread_ref": thread_ref,
        "message_ref": message_handle(message, student_id=student_id, key=key),
        "sender": shorten_name(message.get("sender")),
        "sent_received_date_text": message.get("sent_received_date_text") or "",
        "body": message.get("body_text") or "",
        "server_read_state": "unread" if message.get("is_unread") else "read",
        "attachments": message.get("attachments") or [],
        "additional_link_url": message.get("additional_link_url") or None,
        "retention": "not_stored_by_ag_school",
    }
