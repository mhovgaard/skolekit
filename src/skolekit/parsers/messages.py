from __future__ import annotations

import hashlib
import json
import re
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from skolekit.parsers.weekly_plan import content_to_text


INBOX_SELECTOR = (
    "[data-clientlogic-settings-messageconversationswitharchivefunctionality]"
)
INBOX_ATTRIBUTE = (
    "data-clientlogic-settings-messageconversationswitharchivefunctionality"
)
# The first segment is the school's portal ([site] portal, 'parent' by default).
INBOX_URL_PATTERN = re.compile(
    r"/[a-z]+/(?P<student_id>\d+)/(?P<child>[^/]+)/messages/conversations/?$",
    flags=re.I,
)
READ_ONLY_ENDPOINT_KEYS = {
    "ExpandThreadMessagesUrl",
    "GetMessageForThreadlessConversationUrl",
    "GetNewConversationsUrl",
    "LoadMessagesForSelectedConversationUrl",
}
PARENT_CONTEXT_PATTERN = re.compile(r"\([^)]*\d[^)]*\)\s*$")
STAFF_INITIALS_PATTERN = re.compile(r"\([A-ZÆØÅ]{1,5}\)\s*$")


class MessageParseError(ValueError):
    """Raised when the expected embedded message payload is unavailable."""


def sender_role_hint(
    sender: str | None, *, is_system: bool = False, is_outbox: bool = False
) -> dict[str, str]:
    """Return conservative, explainable hints; final roles require classification."""
    if is_outbox:
        return {"role": "self", "evidence": "message_is_outbox"}
    if is_system:
        return {"role": "system", "evidence": "message_is_system"}
    value = (sender or "").strip()
    if PARENT_CONTEXT_PATTERN.search(value):
        return {
            "role": "parent_or_guardian_hint",
            "evidence": "participant_label_contains_class_or_child_number",
        }
    if STAFF_INITIALS_PATTERN.search(value):
        return {
            "role": "school_staff_hint",
            "evidence": "participant_label_looks_like_staff_initials",
        }
    return {"role": "unknown", "evidence": "no_deterministic_role_signal"}


def normalize_profile(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    return {
        "name": raw.get("Name"),
        "initials": raw.get("Initials"),
        "photo": raw.get("Photo"),
        "access_allowed": raw.get("AccessAllowed"),
    }


def normalize_conversation(raw: dict[str, Any]) -> dict[str, Any]:
    participant_text = raw.get("ThreadParticipantsString") or ""
    return {
        "thread_id": raw.get("ThreadId"),
        "subject": raw.get("Subject") or "",
        "date_text": raw.get("Date") or "",
        "message_count": raw.get("MessagesCount") or 0,
        "latest_message_id": raw.get("LatestMessageId"),
        "latest_inbox_message_id": raw.get("LatestInboxMessageId"),
        "is_unread": bool(raw.get("IsUnread")),
        "unread_message_count": raw.get("UnreadMessagesCount") or 0,
        "has_attachments": bool(raw.get("HasAttachments")),
        "is_replied": bool(raw.get("IsReplied")),
        "is_forwarded": bool(raw.get("IsForwarded")),
        "is_school_board_related": bool(raw.get("IsSchoolBoardRelated")),
        "participants_text": participant_text,
        "participant_role_hint": sender_role_hint(participant_text),
        "profile": normalize_profile(raw.get("ProfileImageViewModel")),
    }


def conversation_fingerprint(conversation: dict[str, Any]) -> dict[str, Any]:
    return {
        key: conversation.get(key)
        for key in (
            "latest_message_id",
            "latest_inbox_message_id",
            "message_count",
            "is_unread",
            "unread_message_count",
            "is_replied",
        )
    }


def _same_origin_url(base_url: str, endpoint: str) -> str:
    resolved = urljoin(base_url, endpoint)
    base = urlparse(base_url)
    target = urlparse(resolved)
    if target.scheme != "https" or target.netloc != base.netloc:
        raise MessageParseError("Message endpoint was outside the inbox origin")
    return resolved


def parse_inbox_html(html: str, source_url: str) -> dict[str, Any]:
    route = INBOX_URL_PATTERN.search(urlparse(source_url).path)
    if not route:
        raise MessageParseError("URL is not a message-conversations route")

    root = BeautifulSoup(html, "html.parser").select_one(INBOX_SELECTOR)
    if root is None:
        raise MessageParseError("Message settings element was not found")
    encoded = root.get(INBOX_ATTRIBUTE)
    if not encoded:
        raise MessageParseError("Message settings payload was empty")
    try:
        payload = json.loads(encoded)
    except (json.JSONDecodeError, TypeError) as error:
        raise MessageParseError("Message settings payload was invalid") from error

    settings = payload.get("DataProviderSettings") or {}
    endpoints = {
        key: _same_origin_url(source_url, settings[key])
        for key in READ_ONLY_ENDPOINT_KEYS
        if settings.get(key)
    }
    if set(endpoints) != READ_ONLY_ENDPOINT_KEYS:
        raise MessageParseError("Required read-only message endpoints were unavailable")

    normalized: dict[str, Any] = {
        "schema_version": 1,
        "source": {
            "url": source_url,
            "student_id": int(route.group("student_id")),
            "child": route.group("child"),
        },
        "conversations": [
            normalize_conversation(item)
            for item in payload.get("Conversations") or []
            if isinstance(item, dict)
        ],
        "read_only_endpoints": endpoints,
        "search_request": settings.get("SearchRequest") or "",
        "filter": "",
    }
    canonical = json.dumps(normalized, ensure_ascii=False, sort_keys=True).encode()
    normalized["source_hash"] = hashlib.sha256(canonical).hexdigest()
    return normalized


def normalize_recipients(raw: Any) -> dict[str, list[Any]]:
    if not isinstance(raw, dict):
        return {"to": [], "cc": [], "bcc": []}
    return {
        "to": raw.get("") if isinstance(raw.get(""), list) else [],
        "cc": raw.get("Cc") if isinstance(raw.get("Cc"), list) else [],
        "bcc": raw.get("Bcc") if isinstance(raw.get("Bcc"), list) else [],
    }


def normalize_message(raw: dict[str, Any]) -> dict[str, Any]:
    body_html = raw.get("BaseText") or ""
    previous_html = raw.get("PreviousMessagesText") or ""
    sender = raw.get("SenderName") or ""
    is_system = bool(raw.get("IsSystemMessage"))
    is_outbox = bool(raw.get("IsOutbox"))
    normalized = {
        "message_id": raw.get("Id"),
        "subject": raw.get("Subject") or "",
        "sender": sender,
        "sender_role_hint": sender_role_hint(
            sender, is_system=is_system, is_outbox=is_outbox
        ),
        "sent_received_date_text": raw.get("SentReceivedDateText") or "",
        "body_html": body_html,
        "body_text": content_to_text(body_html),
        "previous_messages_html": previous_html,
        "previous_messages_text": content_to_text(previous_html),
        "additional_link_url": raw.get("AdditionalLinkUrl") or "",
        "attachments": raw.get("AttachmentsLinks") or [],
        "recipients": normalize_recipients(raw.get("Recipients")),
        "is_system": is_system,
        "is_outbox": is_outbox,
        "is_copied_to_archive": bool(raw.get("IsCopiedToArchive")),
        "is_unread": bool(raw.get("ShowUnreadIndication")),
        "auto_deletion_date_text": raw.get("AutoDeletionDateText") or "",
    }
    canonical = json.dumps(normalized, ensure_ascii=False, sort_keys=True).encode()
    normalized["source_hash"] = hashlib.sha256(canonical).hexdigest()
    return normalized


def inbox_metadata(inbox: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": inbox["source"],
        "conversation_count": len(inbox["conversations"]),
        "unread_conversation_count": sum(
            1 for item in inbox["conversations"] if item["is_unread"]
        ),
        "conversations": inbox["conversations"],
        "source_hash": inbox["source_hash"],
    }
