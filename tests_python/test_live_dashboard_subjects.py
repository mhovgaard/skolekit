import html
import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "rendering/shared"))
import live_subjects as live
from dashboard import render_html, validate
from tests_python.synthetic_household import CHILDREN
from tests_python.test_dashboard_rendering import state


@pytest.mark.parametrize("subject", [
    "Ugeplan uge 39", "Høstfest torsdag kl. 15-17", "Husk syltetøjsglas og avis i morgen",
    "SV: Arbejdslørdag 19/9", "Idræt mandag", "Forældremøde for 0. klasse",
    "Information om spilledag og fredag i denne uge", "En ukendt formulering",
    "Invitation til Emmas fødselsdag", "Lektier til Emma Jensen",
    "Personalets dag - hemmelighed", "Personalets dag – hemmelighed 🤫",
    # The household shows general illness and staff changes (7 November 2026).
    "Idræt: medicin", "Høstfest: allergi", "Ny lærer i klassen", "Influenza i klassen",
    "Lusetjek efter ferien",
])
def test_plain_school_subjects_are_allowed(subject):
    assert live.checked_subject(subject) == ("safe", subject)


@pytest.mark.parametrize("subject", [
    "Ugeplan og ADHD", "Bekymring om trivsel", "Lærerens præstation",
    "Forældremøde om relationer", "Konflikt mellem børn",
    "School event / mental health", "Samtale om faglig udvikling", "Husk CPR 123456-7890",
    "Ugeplan <script>alert(1)</script>", "Ugeplan\u202eADHD", "Ugeplan\ntrivsel",
    "Vedr. Emma Jensen", "Betaling mangler",
    "Ugeplan 12 34 56 78 90",
    "Ugeplan " + "ugeplan " * 12 + "fortrolig diagnose",
])
def test_sensitive_and_uncertain_subjects_fail_closed(subject):
    assert live.checked_subject(subject) == ("redacted", "")


def test_subject_is_checked_before_truncation_and_never_enters_saved_state():
    data = state()
    before = json.dumps(data)
    row = live.display_message("ChildA", {"SenderName": "Emilie Hansen", "Subject": "Ugeplan " * 12 + "ADHD"})
    assert row.sender == "Emilie H." and row.status == "redacted"
    output = render_html(data, live_messages=[row])
    assert "Emilie H. (redacted)" in output
    assert "ADHD" not in output and "Ugeplan" not in output
    assert "ADHD" not in repr(row)
    assert json.dumps(data) == before
    data["messages"]["items"][0]["subject"] = "Ugeplan"
    with pytest.raises(ValueError):
        validate(data)


def test_safe_subject_is_bounded_before_html_embedding():
    row = live.display_message("ChildA", {"SenderName": "Emilie Hansen", "Subject": "Ugeplan " * 12})
    assert row.status == "safe" and len(row.text) <= 44 and row.text.endswith("…")
    output = render_html(state(), live_messages=[row])
    assert row.text in output
    assert "Ugeplan " * 12 not in output


def test_named_subject_uses_harmless_context_and_recipient_breadth():
    invite = live.display_message("Alfa", {
        "SenderName": "Sanne Madsen", "Subject": "Invitation til Augusts spilledag",
        "Recipients": {"": [{"Name": "Augusts klasse"}]},
    })
    assert invite.status == "safe" and invite.text == "Invitation til Augusts spilledag"

    individual = live.display_message("Alfa", {
        "SenderName": "Sanne Madsen", "Subject": "Vedrørende Alfa",
        "Recipients": {"": [{"Name": "Familien"}]},
    })
    assert individual.status == "redacted"

    broad = live.display_message("Alfa", {
        "SenderName": "Sanne Madsen", "Subject": "Vedrørende Alfa",
        "Recipients": {"": [{"Name": "0. klasse"}]},
    })
    assert broad.status == "safe"

    broad_sensitive_wording = live.display_message("Alfa", {
        "SenderName": "Sanne Madsen", "Subject": "Trivsel i klassen",
        "Recipients": [{"Name": "Alle forældre i 0. klasse"}],
    })
    assert broad_sensitive_wording.status == "safe"

    named_event = live.display_message("Alfa", {
        "SenderName": "Sanne Madsen", "Subject": "Fødselsdag for Alfa",
        "Recipients": [{"Name": "Familien"}],
    })
    assert named_event.status == "safe"


def inbox_payload():
    return {"Conversations": [
        {"ThreadId": 10, "Subject": "OLD THREAD TITLE", "LatestMessageId": 12, "LatestInboxMessageId": 11}
    ], "DataProviderSettings": {
        "ExpandThreadMessagesUrl": "/expand", "GetMessageForThreadlessConversationUrl": "/single",
        "GetNewConversationsUrl": "/page", "LoadMessagesForSelectedConversationUrl": "/load",
    }}


def test_fetch_exact_latest_subject_and_sender_ignoring_bodies_and_thread_title(capsys):
    requests = []
    def handle(request):
        requests.append(request)
        if request.url.path.endswith("conversations"):
            encoded = html.escape(json.dumps(inbox_payload()), quote=True)
            return httpx.Response(200, text=f'<div data-clientlogic-settings-messageconversationswitharchivefunctionality="{encoded}"></div>')
        assert request.url.path == "/load"
        assert request.url.params["takeFromRootMessageId"] == request.url.params["takeToMessageId"] == "12"
        return httpx.Response(200, json=[{"Id": 12, "Subject": "Ugeplan uge 39", "SenderName": "Emilie Hansen",
                                         "BaseText": "SECRET BODY CANARY", "PreviousMessagesText": "SECRET HISTORY"}])
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        row = live.fetch_latest(client, CHILDREN[0])
    assert len(requests) == 2 and all(r.method == "GET" for r in requests)
    assert row.text == "Ugeplan uge 39" and row.sender == "Emilie H."
    output = render_html(state(), live_messages=[row])
    assert "SECRET" not in output and "OLD THREAD TITLE" not in output
    assert capsys.readouterr().out == ""


def test_retrieval_failure_is_per_child_and_does_not_echo_content(monkeypatch, capsys):
    from skolekit.auth.session import SessionStore
    SessionStore().save({"cookies": []})  # in this test's own state directory
    def fetch(client, child):
        if child.child == "Alfa":
            raise RuntimeError("PRIVATE SUBJECT CANARY")
        return live.DisplayMessage(child.child, "Emilie H.", "Ugeplan", "safe")
    monkeypatch.setattr(live, "fetch_latest", fetch)
    rows = live.load_live_messages(["Alfa", "Beta"])
    assert [r.status for r in rows] == ["unavailable", "safe"]
    output = render_html(state(), live_messages=rows)
    assert "Emne utilgængeligt" in output and "PRIVATE SUBJECT CANARY" not in output
    assert capsys.readouterr().out == ""
