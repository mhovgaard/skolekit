"""Messages: the inbox and its threads, read live.

Message text is never stored, so messages are not a harvest source. The
fetchers here read the inbox and a thread live when someone asks, and the
household's restricted collection reads the inbox's metadata for its event
extraction. The legacy mode, which stored messages as records, was removed on
14 November 2026.
"""
from __future__ import annotations

from typing import Any

import httpx

from skolekit.parsers.messages import (
    MessageParseError,
    normalize_conversation,
    normalize_message,
)

def fetch_thread_messages(
    client: httpx.Client, inbox: dict[str, Any], conversation: dict[str, Any]
) -> list[dict[str, Any]]:
    """Fetch a thread using GET-only endpoints that do not mutate read state."""
    endpoints = inbox["read_only_endpoints"]
    if not conversation["thread_id"]:
        response = client.get(
            endpoints["GetMessageForThreadlessConversationUrl"],
            params={"messageId": conversation["latest_message_id"]},
        )
        response.raise_for_status()
        message = response.json()
        return [normalize_message(message)] if isinstance(message, dict) else []
    headers_response = client.get(
        endpoints["ExpandThreadMessagesUrl"],
        params={
            "threadId": conversation["thread_id"],
            "latestMessageId": conversation["latest_message_id"],
        },
    )
    headers_response.raise_for_status()
    headers = headers_response.json()
    if not isinstance(headers, list) or not headers:
        return []

    messages_response = client.get(
        endpoints["LoadMessagesForSelectedConversationUrl"],
        params={
            "threadId": conversation["thread_id"],
            "takeFromRootMessageId": headers[0].get("MessageId"),
            "takeToMessageId": headers[-1].get("MessageId"),
            "searchRequest": inbox.get("search_request") or "",
        },
    )
    messages_response.raise_for_status()
    messages = messages_response.json()
    if not isinstance(messages, list):
        raise MessageParseError("Thread message response was not a list")
    return [normalize_message(item) for item in messages if isinstance(item, dict)]


def fetch_all_conversations(
    client: httpx.Client,
    inbox: dict[str, Any],
    *,
    max_pages: int = 100,
    page_limit: int | None = None,
) -> tuple[list[dict[str, Any]], bool, bool, int]:
    """Fetch a complete inbox or a bounded number of total conversation pages.

    The server-rendered inbox is page one. ``page_limit=1`` therefore performs no
    pagination call. The first boolean reports completion of the requested scope;
    the second reports whether the global inbox was exhausted.
    """
    if page_limit is not None and page_limit < 1:
        raise ValueError("Message page limit must be at least 1")
    conversations = list(inbox["conversations"])
    def identity(item: dict[str, Any]) -> str:
        return (
            f"thread:{item['thread_id']}"
            if item["thread_id"]
            else f"message:{item['latest_message_id']}"
        )

    seen_threads = {identity(item) for item in conversations}
    seen_cursors: set[Any] = set()
    exhausted = not conversations
    pages = 1
    additional_limit = (
        max_pages if page_limit is None else min(max_pages, page_limit - 1)
    )

    for _ in range(additional_limit):
        if not conversations:
            exhausted = True
            break
        cursor = conversations[-1]["latest_inbox_message_id"]
        if not cursor or cursor in seen_cursors:
            break
        seen_cursors.add(cursor)
        response = client.get(
            inbox["read_only_endpoints"]["GetNewConversationsUrl"],
            params={
                "takeFromRootMessageId": cursor,
                "searchRequest": inbox.get("search_request") or "",
                "filter": inbox.get("filter") or "",
            },
        )
        response.raise_for_status()
        page = response.json()
        if not isinstance(page, list):
            raise MessageParseError("Conversation page response was not a list")
        if not page:
            exhausted = True
            break
        pages += 1
        added = 0
        for raw in page:
            if not isinstance(raw, dict):
                continue
            normalized = normalize_conversation(raw)
            normalized_identity = identity(normalized)
            if normalized_identity in seen_threads:
                continue
            seen_threads.add(normalized_identity)
            conversations.append(normalized)
            added += 1
        if not added:
            break
    scope_complete = exhausted or (page_limit is not None and pages >= page_limit)
    return conversations, scope_complete, exhausted, pages
