"""Ephemeral live subject overlay. No database, files or logging.

With the household's opt-in (`AG_MESSAGE_TOPICS=model`, see `message_topics`) a
subject that passed the local check is sent to the model through the
zero-data-retention route, which returns a one- or two-word topic or hides it.
Without the opt-in no subject leaves this process.
"""
from __future__ import annotations

import re
import sys
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


@dataclass(frozen=True, repr=False)
class DisplayMessage:
    child: str
    sender: str
    text: str
    status: str  # safe, redacted, unavailable, empty
    activity: str = ""
    unread: bool = False
    topic: str = ""


# Ordinary school subjects are readable by default. The household hides only
# wellbeing, relations between children, mental health and diagnoses, an
# individual child's performance or behaviour, family matters and payments.
# General illness (lice, flu) and staff changes may show (7 November 2026).
SENSITIVE = re.compile(
    r"trivsel|mistriv|bekymr|konflikt|klag|mob|diagnos|adhd|autis|angst|depress|"
    r"læsebesvær|dysleks|ordblind|psyk|mental|præstation|faglig udvikling|adfærd|relation|"
    r"skilsm|samarbejdsproblem|vold|overgreb|støttebehov|underret|fortrolig|"
    r"personlig|privat|ppr|cpr|karakter|økonomi|betaling|restance|gæld|faktura|kontingent|"
    r"assessment|bully|concern|performance|relationship", re.I
)
HARMLESS_CONTEXT = re.compile(
    r"invitation|inviter|fødselsdag|fest|arrangement|spilledag|brætspil|legeaftale|"
    r"tur|udflugt|høstfest|arbejdslørdag|forældremøde|klassefest|svømmedag|idræt|"
    r"morgensang|bibliotek|ugeplan|ugebrev|lektie|hjemmearbejde|husk|medbring|"
    r"praktisk|information|info|ferie|skolefoto|fotografering|aflyst|ændret|"
    r"flyttet|tilmelding|påmindelse|personalets dag|hemmelighed|overraskelse", re.I
)
BROAD_AUDIENCE = re.compile(
    r"klasse|årgang|alle|forældre|gruppen|holdet|sfo|indskoling|mellemtrin", re.I
)
PERSONAL_REFERENCE = re.compile(
    r"\b(?:[Oo]m|[Vv]edr(?:ørende)?\.?|[Aa]ngående|[Tt]il)\s+"
    r"(?:[A-ZÆØÅ][a-zæøå]+)(?:\s+[A-ZÆØÅ][a-zæøå]+)?\b"
)
CONTACT_OR_MARKUP = re.compile(
    r"<[^>]*>|https?://|www\.|\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b", re.I
)


def _broad_recipient_audience(raw: object) -> bool:
    entries = []
    labels = []

    def visit(value):
        if isinstance(value, dict):
            label = value.get("Name") or value.get("name") or value.get("Label") or value.get("label")
            if label:
                entries.append(value)
                labels.append(str(label))
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
        elif isinstance(value, str):
            labels.append(value)

    visit(raw)
    if len(entries) >= 3:
        return True
    return bool(BROAD_AUDIENCE.search(" ".join(labels)))


def checked_subject(subject: object, *, child: str = "", broad_audience: bool = False) -> tuple[str, str]:
    """Inspect the full subject before shortening under the household policy."""
    if not isinstance(subject, str) or not subject.strip():
        return "unavailable", "Emne utilgængeligt"
    normalized = unicodedata.normalize("NFKC", subject)
    if (len(normalized) > 300
            or any(unicodedata.category(c).startswith("C") for c in normalized)
            or CONTACT_OR_MARKUP.search(normalized)):
        return "redacted", ""
    # Do not disclose phone/identity-like digit sequences separated by punctuation.
    if len(re.sub(r"\D", "", normalized)) > 8:
        return "redacted", ""
    if SENSITIVE.search(normalized) and not broad_audience:
        return "redacted", ""
    personal = bool(PERSONAL_REFERENCE.search(normalized))
    if child and re.search(rf"\b{re.escape(child)}\b", normalized, flags=re.IGNORECASE):
        personal = True
    if personal and not broad_audience and not HARMLESS_CONTEXT.search(normalized):
        return "redacted", ""
    return "safe", re.sub(r"\s+", " ", normalized).strip()


def short_text(value: str, limit: int = 44) -> str:
    if len(value) <= limit:
        return value
    return value[:limit-1].rstrip() + "…"


def display_message(child: str, raw: dict) -> DisplayMessage:
    from skolekit.privacy import normalize_latest_activity, shorten_name

    sender = shorten_name(raw.get("SenderName"))
    if not re.fullmatch(r"[^\s.]{1,30} [A-ZÆØÅ]\.", sender):
        sender = "Afsender ukendt"
    status, text = checked_subject(
        raw.get("Subject"), child=child,
        broad_audience=_broad_recipient_audience(raw.get("Recipients")),
    )
    # Store only the approved, bounded fragment in the transient rendering object.
    activity = normalize_latest_activity(raw.get("SentReceivedDateText"),
                                         observed_on=datetime.now(ZoneInfo("Europe/Copenhagen")).date())
    if activity:
        value = datetime.fromisoformat(activity)
        activity = value.strftime("%d/%m") + (value.strftime(" %H:%M") if "T" in activity else "")
    return DisplayMessage(child, sender, short_text(text), status, activity or "",
                          bool(raw.get("ShowUnreadIndication")))


def fetch_latest(client, child) -> DisplayMessage:
    """Use GET-only endpoints; require the exact latest ID, never an older fallback."""
    from skolekit.parsers.messages import parse_inbox_html
    from skolekit.transport import is_authenticated_page

    response = client.get(child.route("messages/conversations"))
    response.raise_for_status()
    if not is_authenticated_page(str(response.url)):
        raise ValueError("session_unavailable")
    inbox = parse_inbox_html(response.text, str(response.url))
    conversations = inbox["conversations"]
    if not conversations:
        return DisplayMessage(child.child, "", "Ingen beskeder fundet", "empty")
    # The same server inbox ordering used by `message get --child --latest`.
    conversation = conversations[0]
    latest = conversation["latest_message_id"]
    if latest is None:
        raise ValueError("latest_unavailable")
    endpoints = inbox["read_only_endpoints"]
    if conversation["thread_id"]:
        response = client.get(endpoints["LoadMessagesForSelectedConversationUrl"], params={
            "threadId": conversation["thread_id"], "takeFromRootMessageId": latest,
            "takeToMessageId": latest, "searchRequest": inbox.get("search_request") or "",
        })
        response.raise_for_status()
        rows = response.json()
    else:
        response = client.get(endpoints["GetMessageForThreadlessConversationUrl"], params={"messageId": latest})
        response.raise_for_status()
        rows = [response.json()]
    matches = [r for r in rows if isinstance(r, dict) and str(r.get("Id")) == str(latest)]
    if len(matches) != 1 or matches[0].get("IsCopiedToArchive"):
        raise ValueError("latest_unavailable")
    raw = matches[0]
    # The school response may include a body; never normalize, inspect or persist it.
    selected = {k: raw.get(k) for k in (
        "Subject", "SenderName", "SentReceivedDateText", "ShowUnreadIndication", "Recipients",
    )}
    del raw, matches, rows, response, inbox, conversations, conversation
    return display_message(child.child, selected)


def with_topics(messages: list[DisplayMessage], write_topics) -> list[DisplayMessage]:
    """Each safe subject's topic from `write_topics`; a hidden one becomes redacted.

    `write_topics(subjects, names)` returns one `(topic or None, hide)` per subject.
    Any failure leaves the messages without topics, so the board shows sender and date.
    """
    from dataclasses import replace

    safe = [index for index, item in enumerate(messages) if item.status == "safe" and item.text]
    if not safe:
        return messages
    try:
        answers = write_topics([messages[index].text for index in safe], [item.child for item in messages])
    except Exception:
        return messages
    result = list(messages)
    for index, (topic, hide) in zip(safe, answers):
        if hide:
            result[index] = replace(result[index], text="", status="redacted")
        elif topic:
            result[index] = replace(result[index], topic=topic)
    return result


def load_live_messages(child_names: list[str], *, topics=None) -> list[DisplayMessage]:
    """The latest message per child; with `topics` (see `with_topics`) each gets a short topic."""
    result = _load_live_messages(child_names)
    return with_topics(result, topics) if topics is not None else result


def _load_live_messages(child_names: list[str]) -> list[DisplayMessage]:
    # Optional network path; plain HTML rendering still needs only the stdlib.
    unavailable = lambda name: DisplayMessage(name, "", "Emne utilgængeligt", "unavailable")
    try:
        from skolekit.auth.session import SessionStore, school_client
        from skoleklar.household import load_site_profile
        state = SessionStore().load()
        if state is None:
            return [unavailable(n) for n in child_names]
        profile = load_site_profile()
        configs = {child.child: child for child in profile.children}
        result = []
        with school_client(state, profile.school.host) as client:
            for name in child_names:
                try:
                    result.append(fetch_latest(client, configs[name]))
                except Exception:
                    # Content-free fallback. Never print an exception/response or
                    # reuse a subject from a previous render.
                    result.append(unavailable(name))
        return result
    except Exception:
        return [unavailable(n) for n in child_names]
