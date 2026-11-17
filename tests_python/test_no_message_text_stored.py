"""Phase 8: no message text reaches the database, end to end.

A whole harvest on the synthetic site, with the restricted message collection
and the event extraction turned on, then a search of every byte of the
database and its write-ahead log for the messages' subjects and bodies. The
model is a stand-in that reports what it was given, so the test also proves
the text did reach the extraction, in memory, and still was not stored.
"""
import asyncio
import json
from pathlib import Path

import httpx
import pytest

from skoleklar import pipeline
from skoleklar.pipeline import HarvestRequest
from skolekit.auth.session import SessionStore
from skolekit.sources import calendar as calendar_source
from skolekit.transport import ReadOnlyClient
from tests_python.skoleintra_site import FIXTURES, SyntheticSite, frozen_datetime


def message_texts() -> list[str]:
    """Every subject and body line the synthetic inbox holds, long enough to be text."""
    texts = set()
    for child in ("alfa", "beta"):
        folder = FIXTURES / child
        for name in ("messages-inbox.json", "messages-pages.json", "messages-threads.json",
                     "messages-threadless.json"):
            path = folder / name
            if not path.exists():
                continue
            stack = [json.loads(path.read_text(encoding="utf-8"))]
            while stack:
                node = stack.pop()
                if isinstance(node, dict):
                    for key, value in node.items():
                        if key in ("Subject", "BaseText", "PreviousMessagesText") and isinstance(value, str):
                            texts |= {line.strip() for line in value.replace("<br>", "\n").splitlines()}
                        else:
                            stack.append(value)
                elif isinstance(node, list):
                    stack.extend(node)
    # Text the school also published (a notice, a plan) is rightly stored; look only for the rest.
    published = "".join(path.read_text(encoding="utf-8") for path in FIXTURES.rglob("*")
                        if path.is_file() and not path.name.startswith("messages-"))
    return sorted(text for text in texts if len(text) >= 12 and "<" not in text and text not in published)


def harvest(monkeypatch, tmp_path, answer):
    """A whole harvest with message processing; `answer(document)` is the stand-in model's items."""
    monkeypatch.setattr(calendar_source, "datetime", frozen_datetime())
    site = SyntheticSite()
    SessionStore().save({"cookies": []})
    monkeypatch.setattr(pipeline, "session_is_valid", lambda state, url: True)
    monkeypatch.setattr(pipeline, "school_client",
                        lambda state, host: ReadOnlyClient(host, transport=httpx.MockTransport(site.handle)))
    monkeypatch.setattr(pipeline, "openrouter_api_key", lambda: "synthetic-key")
    monkeypatch.setenv("AG_SCHOOL_MESSAGE_HANDLE_KEY", "c3ludGhldGljLWhhbmRsZS1rZXktZm9yLXRlc3RzLW9ubHktMzI=")
    seen: list[str] = []

    class Model:
        def __init__(self, **_kwargs):
            pass

        def generate(self, document, **_kwargs):
            seen.append(json.dumps(document, ensure_ascii=False))
            return {"response_id": "synthetic", "usage": {}, "output": {"items": answer(document)}}

    monkeypatch.setattr("skoleklar.triage.message_events.OpenRouterClient", Model)
    database = tmp_path / "school-restricted.db"
    request = HarvestRequest(db=database, mode="initialize", message_mode="events-only",
                             message_processor="zdr", progress="off")
    result = asyncio.run(pipeline.harvest(request))
    assert result.exit_code == 0, result.output
    durable = b"".join(path.read_bytes() for path in sorted(Path(tmp_path).glob("school-restricted.db*")))
    return seen, durable, database


def test_a_harvest_with_message_processing_stores_no_message_text(monkeypatch, tmp_path):
    seen, durable, _ = harvest(monkeypatch, tmp_path, lambda document: [])
    texts = message_texts()
    assert len(texts) >= 3
    # The extraction saw the messages, in memory.
    assert any(text in document for document in seen for text in texts)
    stored = [text for text in texts if text.encode("utf-8") in durable]
    assert stored == [], f"{len(stored)} message text(s) in the database"


def test_a_birthday_invitation_stores_the_birthday_childs_first_name_and_no_text(monkeypatch, tmp_path):
    """restricted-messages-v5: one first name per birthday party, and still no message text."""
    import re
    import sqlite3

    from skoleklar.dashboard_events import CELEBRANT

    named = set()

    def answer(document):
        # Every message 'invites' to a party, named after a capitalised word of its own text.
        body = document["body_text"].strip()
        word = next((w for w in re.findall(r"[A-ZÆØÅ][a-zæøå]{3,}", body) if CELEBRANT.fullmatch(w)), None)
        if not body or word is None:
            return []
        named.add(word)
        return [{"dedupe_hint": "party", "event_kind": "event", "activity_kind": "birthday_party",
                 "celebrant": word, "effective_date": document["message_date"], "end_date": None,
                 "all_day": True, "cancellation_state": "active", "necessary_location": None,
                 "bring_items": [], "requirement": None, "confidence": 0.9, "needs_review": False,
                 "evidence_quote": body[:24]}]

    _, durable, database = harvest(monkeypatch, tmp_path, answer)
    with sqlite3.connect(database) as connection:
        kept = {row[0] for row in connection.execute(
            "SELECT celebrant FROM message_event_facts WHERE celebrant IS NOT NULL")}
    assert kept and kept <= named
    stored = [text for text in message_texts() if text.encode("utf-8") in durable]
    assert stored == [], f"{len(stored)} message text(s) in the database"
