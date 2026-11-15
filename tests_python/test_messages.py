import html
import json

from skolekit.sources.messages import fetch_all_conversations
from skolekit.parsers.messages import (
    normalize_message,
    parse_inbox_html,
    sender_role_hint,
)


SOURCE_URL = (
    "https://school.m.skoleintra.dk/parent/9202/Beta/messages/conversations"
)


def inbox_document(payload: dict) -> str:
    encoded = html.escape(json.dumps(payload), quote=True)
    return (
        '<div data-clientlogic-settings-messageconversationswitharchivefunctionality="'
        f'{encoded}"></div>'
    )


def test_parses_inbox_and_only_exposes_read_endpoints() -> None:
    payload = {
        "Conversations": [
            {
                "ThreadId": 10,
                "Subject": "Class update",
                "Date": "19. aug.",
                "MessagesCount": 2,
                "LatestMessageId": 12,
                "LatestInboxMessageId": 12,
                "IsUnread": True,
                "UnreadMessagesCount": 1,
                "HasAttachments": False,
                "ThreadParticipantsString": "Teacher (AB)",
            }
        ],
        "DataProviderSettings": {
            "ExpandThreadMessagesUrl": "/parent/messages/expand",
            "GetMessageForThreadlessConversationUrl": "/parent/messages/single",
            "GetNewConversationsUrl": "/parent/messages/page",
            "LoadMessagesForSelectedConversationUrl": "/parent/messages/load",
            "ChangeMessageReadStatusUrl": "/parent/messages/change-read",
            "BatchDeleteConversationUrl": "/parent/messages/delete",
            "SearchRequest": "",
        },
    }

    result = parse_inbox_html(inbox_document(payload), SOURCE_URL)

    assert result["source"]["student_id"] == 9202
    assert result["conversations"][0]["subject"] == "Class update"
    assert result["conversations"][0]["participant_role_hint"]["role"] == (
        "school_staff_hint"
    )
    assert set(result["read_only_endpoints"]) == {
        "ExpandThreadMessagesUrl",
        "GetMessageForThreadlessConversationUrl",
        "GetNewConversationsUrl",
        "LoadMessagesForSelectedConversationUrl",
    }
    assert "change-read" not in str(result["read_only_endpoints"])
    assert "delete" not in str(result["read_only_endpoints"])


def test_normalizes_message_body_and_preserves_traceability() -> None:
    result = normalize_message(
        {
            "Id": 22,
            "Subject": "Trip",
            "BaseText": "<p>Bring boots<br>Wednesday</p>",
            "PreviousMessagesText": "<p>Earlier note</p>",
            "SenderName": "Parent (Ella 02)",
            "SentReceivedDateText": "19. aug. 08:00",
            "Recipients": {"": [{"Name": "Family"}], "Cc": [], "Bcc": []},
            "IsSystemMessage": False,
            "IsOutbox": False,
            "ShowUnreadIndication": True,
        }
    )

    assert result["message_id"] == 22
    assert result["body_text"] == "Bring boots\nWednesday"
    assert result["previous_messages_text"] == "Earlier note"
    assert result["recipients"]["to"] == [{"Name": "Family"}]
    assert result["sender_role_hint"]["role"] == "parent_or_guardian_hint"
    assert result["is_unread"] is True
    assert len(result["source_hash"]) == 64


def test_sender_role_hints_are_conservative_and_explainable() -> None:
    assert sender_role_hint("anything", is_system=True)["role"] == "system"
    assert sender_role_hint("anything", is_outbox=True)["role"] == "self"
    assert sender_role_hint("A person")["role"] == "unknown"


def test_recent_message_page_limit_counts_server_rendered_page() -> None:
    class NoRequestsClient:
        def get(self, *_args, **_kwargs):
            raise AssertionError("page_limit=1 must not request another page")

    inbox = {
        "conversations": [
            {
                "thread_id": "thread-1",
                "latest_message_id": 10,
                "latest_inbox_message_id": 10,
            }
        ],
        "read_only_endpoints": {"GetNewConversationsUrl": "https://school/page"},
        "search_request": "",
        "filter": "",
    }

    conversations, complete, exhausted, pages = fetch_all_conversations(
        NoRequestsClient(), inbox, page_limit=1
    )

    assert len(conversations) == 1
    assert complete is True
    assert exhausted is False
    assert pages == 1
