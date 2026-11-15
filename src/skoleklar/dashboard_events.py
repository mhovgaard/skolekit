"""Bounded event classifications and owner-confirmed operational labels."""
import re
from datetime import date

ACTIVITY_LABELS = {
    "class_trip": "Klassetur",
    "class_event": "Klassearrangement",
    "birthday_party": "Fødselsdagsfest",
    "social_activity": "Socialt arrangement",
}


def birthday_notice(item):
    """A calendar birth date establishes no invitation or family attendance."""
    return (item.get("type", item.get("item_type")) == "event"
            and not item.get("private_message_fact")
            and item.get("actionability") == "informational"
            and bool(re.search(r"\bhar fødselsdag\b", str(item.get("title", "")), re.I))
            and not re.search(r"\b(invitation|inviter|fest|fødselsdagsfest|fejring)\b",
                              str(item.get("title", "")) + " " + str(item.get("summary", "")), re.I))


def event_confirmations(document):
    """Only categorical owner input; never accept free-text message content."""
    if document is None:
        return {}
    if (not isinstance(document, dict) or set(document) != {"schema_version", "events"}
            or document["schema_version"] != 1 or not isinstance(document["events"], list)
            or len(document["events"]) > 100):
        raise ValueError("Invalid event confirmation document")
    result = {}
    for event in document["events"]:
        if (not isinstance(event, dict) or set(event) != {"stable_key", "child", "date", "kind"}
                or not isinstance(event["stable_key"], str)
                or not re.fullmatch(r"message-event:[a-f0-9]{64}", event["stable_key"])
                or not isinstance(event["kind"], str) or event["kind"] not in ACTIVITY_LABELS
                or not isinstance(event["date"], str)
                or not isinstance(event["child"], str) or not 1 <= len(event["child"]) <= 24
                or any(ord(c) < 32 for c in event["child"])):
            raise ValueError("Invalid event confirmation")
        date.fromisoformat(event["date"])
        if event["stable_key"] in result:
            raise ValueError("Duplicate event confirmation")
        result[event["stable_key"]] = dict(event)
    return result


def confirmed_activity(item, confirmations):
    confirmation = confirmations.get(item["stable_key"])
    if (confirmation is None or item.get("type") != "event"
            or not item["stable_key"].startswith("message-event:")
            or item.get("child") != confirmation["child"]
            or item.get("effective_date") != confirmation["date"]):
        return None
    return confirmation["kind"]
