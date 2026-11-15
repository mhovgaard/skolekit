"""A one- or two-word topic for each child's latest message, written by the model.

Opt-in per household, chosen at setup: `AG_MESSAGE_TOPICS=model`. Off by default,
and then no subject is ever sent to a model. When on, only subjects that passed
the local check in `live_subjects` are sent, without sender, recipients or body,
through the same zero-data-retention route as the message events. The model
returns a topic or hides it (wellbeing, relations between children, mental health,
one child's performance, family matters, payments). A topic exists only in the
rendered board; it is never stored or logged. See
rendering/ag-school-dashboard-state/references/live-subjects.md.
"""
from __future__ import annotations

import os
import re

from skoleklar.triage.prompt_manifest import load_prompt_manifest

def manifest():
    """The topic stage's manifest with the household's names in its slots."""
    return load_prompt_manifest("message-topic-v1")


TOPICS_ENV = "AG_MESSAGE_TOPICS"
MODES = ("off", "model")
TOPIC_LIMIT = 24
_DIGITS = re.compile(r"\d")


def topics_mode(explicit: str | None = None) -> str:
    value = (explicit or os.environ.get(TOPICS_ENV) or "off").strip().lower()
    if value not in MODES:
        raise ValueError(f"Unsupported message topic mode {value!r}; choose off or model")
    return value


def valid_topic(topic, names: list[str]) -> bool:
    """One or two words, short, no digits and no child's name."""
    if not isinstance(topic, str):
        return False
    words = topic.split()
    if not 1 <= len(words) <= 2 or len(topic) > TOPIC_LIMIT or _DIGITS.search(topic):
        return False
    if any(ord(character) < 32 for character in topic):
        return False
    folded = {word.strip(".,:;!?").casefold() for word in words}
    return not any(name.casefold() in folded for name in names)


def topic_writer(client):
    """`write(subjects, names) -> [(topic or None, hide)]` using `client.generate`."""
    def write(subjects: list[str], names: list[str]) -> list[tuple[str | None, bool]]:
        document = {"subjects": [{"id": str(index), "subject": subject}
                                 for index, subject in enumerate(subjects, 1)]}
        response = client.generate(document, manifest=manifest(), max_output_tokens=400)
        answers = {}
        for entry in (response.get("output") or {}).get("topics") or []:
            if isinstance(entry, dict) and isinstance(entry.get("id"), str):
                answers.setdefault(entry["id"], entry)
        result = []
        for index in range(1, len(subjects) + 1):
            entry = answers.get(str(index), {})
            if entry.get("hide") is True:
                result.append((None, True))
            elif valid_topic(entry.get("topic"), names):
                result.append((entry["topic"].strip(), False))
            else:
                result.append((None, False))
        return result
    return write
