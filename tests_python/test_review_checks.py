"""The review check settles facts flagged for review, stores verdicts and heals the board.
Synthetic facts only; no school data or network."""
from datetime import date

import pytest

from skoleklar import delivery_manifest
from skoleklar.triage import review_checks as rc
from skoleklar.storage import SnapshotStore

TODAY = date(2026, 11, 7)
CLASSES = {"Alfa": 0, "Beta": 2}


def item(key, child, title, *, summary="", day="2026-11-09", flagged=True, reason="Uklart.", quotes=("Kilde",),
         kind="event"):
    return {"stable_key": key, "child": child, "title": title, "summary": summary, "effective_date": day,
            "end_date": None, "item_type": kind, "needs_review": flagged, "review_reason": reason if flagged else None,
            "evidence": [{"quote": quote} for quote in quotes]}


class Client:
    def __init__(self, verdicts=None, error=None):
        self.verdicts, self.error, self.documents = verdicts or [], error, []

    def generate(self, document, **kwargs):
        self.documents.append(document)
        if self.error:
            raise self.error
        return {"output": {"verdicts": self.verdicts}}


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(rc, "child_classes", lambda *args, **kwargs: dict(CLASSES))
    with SnapshotStore(tmp_path / "restricted.db") as snapshot:
        yield snapshot


def with_items(monkeypatch, items):
    monkeypatch.setattr(delivery_manifest, "delivery_items_from_snapshot", lambda store, **kwargs: items)


def test_another_class_is_dropped_unless_it_is_for_everyone():
    """The extractor says which class levels a fact concerns; the words are never read."""
    beta = CLASSES["Beta"]
    other = beta - 1 if beta else beta + 1
    assert rc.rule_verdict(item("a", "Beta", "Skole-hjem-samtaler") | {"class_levels": [other]}, [],
                           CLASSES)[0] == "drop"
    assert rc.rule_verdict(item("c", "Beta", "Skolehjem") | {"class_levels": [beta]}, [], CLASSES) is None
    assert rc.rule_verdict(item("d", "Beta", "Juleklip") | {"class_levels": [other, beta]}, [], CLASSES) is None
    # A class named only in the words decides nothing.
    assert rc.rule_verdict(item("e", "Beta", f"Skole-hjem-samtaler for {other}. klasse"), [], CLASSES) is None


def test_a_vague_fact_is_covered_only_by_a_specific_one_naming_the_child():
    vague = item("a", "Alfa", "Skole-hjem-samtaler", summary="kl. 14.00-19.00")
    window = item("b", "Alfa", "Skole-hjem-samtaler for 0. klasse", summary="kl. 14.00-20.00", flagged=False)
    assert rc.rule_verdict(vague, [vague, window], CLASSES) is None
    own = item("c", "Alfa", "Skole-hjem-samtale for Alfa", summary="Alfa har samtale kl. 15.00", flagged=False)
    assert rc.rule_verdict(vague, [vague, window, own], CLASSES)[0] == "drop"


def test_verdicts_are_stored_reused_and_rechecked_when_the_fact_changes(store, monkeypatch):
    other_class = item("k1", "Beta", "Skole-hjem-samtaler") | {"class_levels": [CLASSES["Beta"] + 1]}
    slot = item("k2", "Beta", "Skolehjemssamtale", reason="Det fremgår ikke, hvilket tidspunkt Beta får.")
    no_evidence = item("k3", "Alfa", "Fødselsdagsfest", quotes=())
    later = item("k4", "Beta", "Skolefest", day="2026-12-20")
    with_items(monkeypatch, [other_class, slot, no_evidence, later])
    client = Client([{"id": "1", "verdict": "show", "note": "tid ikke oplyst", "reason": "Only the slot is unknown."}])
    counts = rc.check_reviews(store, today=TODAY, client=client)
    assert counts == {"flagged": 3, "reused": 0, "rule": 1, "model": 1, "unclear": 0, "unchecked": 1,
                      "model_failed": 0}
    # Only the fact the rules could not settle went to the model, with its evidence.
    assert [entry["title"] for entry in client.documents[0]["items"]] == ["Skolehjemssamtale"]
    assert client.documents[0]["children"] == [{"name": "Alfa", "class_level": "0. klasse"},
                                               {"name": "Beta", "class_level": "2. klasse"}]
    again = Client()
    assert rc.check_reviews(store, today=TODAY, client=again)["reused"] == 2 and again.documents == []
    slot["summary"] = "Samtalerne er flyttet."
    assert rc.check_reviews(store, today=TODAY, client=Client())["reused"] == 1


def test_the_board_applies_show_drop_and_unclear(store, monkeypatch):
    shown = item("k1", "Beta", "Skolehjemssamtale")
    dropped = item("k2", "Beta", "Skole-hjem-samtaler for 0. klasse") | {"class_levels": [CLASSES["Beta"] + 1]}
    unclear = item("k3", "Beta", "Skolefest")
    untouched = item("k4", "Alfa", "Klassetur", flagged=False)
    with_items(monkeypatch, [shown, dropped, unclear, untouched])
    rc.check_reviews(store, today=TODAY, client=Client([
        {"id": "1", "verdict": "show", "note": "tid ikke oplyst", "reason": "Real."},
        {"id": "2", "verdict": "unclear", "note": "Beta: hvornår er festen?", "reason": "Open."},
    ]))
    result = rc.apply_verdicts(store, [shown, dropped, unclear, untouched])
    assert [entry["title"] for entry in result] == ["Skolehjemssamtale · tid ikke oplyst", "Skolefest", "Klassetur"]
    assert [entry["needs_review"] for entry in result] == [False, True, False]


def test_the_phone_manifest_follows_the_same_verdicts(store, monkeypatch):
    def publishable(fact, kind):
        return {**fact, "item_type": kind, "confidence": 0.9, "all_day": True, "summary": "Kilde."}
    shown = publishable(item("k1", "Beta", "Skolehjemssamtale"), "event")
    dropped = publishable(item("k2", "Beta", "Skole-hjem-samtaler for 0. klasse")
                          | {"class_levels": [CLASSES["Beta"] + 1]}, "event")
    unclear = publishable(item("k3", "Beta", "Medbring gummistøvler"), "bring")
    with_items(monkeypatch, [shown, dropped, unclear])
    rc.check_reviews(store, today=TODAY, client=Client([
        {"id": "1", "verdict": "show", "note": "tid ikke oplyst", "reason": "Real."},
        {"id": "2", "verdict": "unclear", "note": "Hvilken dag?", "reason": "Open."},
    ]))
    manifest = delivery_manifest.build_delivery_manifest(
        rc.apply_verdicts(store, [shown, dropped, unclear]), target_date=TODAY)
    assert [entry["title"] for entry in manifest["calendar"]] == ["Beta · Skolehjemssamtale · tid ikke oplyst"]
    assert manifest["reminders"] == []
    assert [entry["stable_key"] for entry in manifest["review"]] == ["k3"]


def test_a_failing_model_keeps_the_rules_verdicts_and_retries_later(store, monkeypatch):
    with_items(monkeypatch, [item("k1", "Beta", "Skole-hjem-samtaler for 0. klasse")
                             | {"class_levels": [CLASSES["Beta"] + 1]},
                             item("k2", "Beta", "Skolehjemssamtale")])
    counts = rc.check_reviews(store, today=TODAY, client=Client(error=RuntimeError("down")))
    assert counts["rule"] == 1 and counts["model_failed"] == 1 and counts["unchecked"] == 1
    assert len(rc.stored_verdicts(store)) == 1


@pytest.mark.parametrize("answer", [
    {"id": "9", "verdict": "show", "note": None, "reason": "Unknown id."},
    {"id": "1", "verdict": "maybe", "note": None, "reason": "Unknown verdict."},
])
def test_invalid_model_answers_are_ignored(store, monkeypatch, answer):
    with_items(monkeypatch, [item("k1", "Beta", "Skolehjemssamtale")])
    counts = rc.check_reviews(store, today=TODAY, client=Client([answer]))
    assert counts["model"] == 0 and counts["unchecked"] == 1 and rc.stored_verdicts(store) == {}


def test_a_long_note_is_dropped_but_the_verdict_kept(store, monkeypatch):
    fact = item("k1", "Beta", "Skolehjemssamtale")
    with_items(monkeypatch, [fact])
    rc.check_reviews(store, today=TODAY, client=Client([
        {"id": "1", "verdict": "show", "note": "x" * 41, "reason": "Real."}]))
    assert rc.apply_verdicts(store, [fact])[0]["title"] == "Skolehjemssamtale"


def test_an_older_database_without_verdicts_changes_nothing(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    sqlite3.connect(path).close()
    with SnapshotStore(path, read_only=True) as old:
        facts = [item("k1", "Beta", "Skolehjemssamtale")]
        assert rc.apply_verdicts(old, facts) == facts


def test_class_levels_come_from_the_harvest_configuration():
    from tests_python.synthetic_household import SCHOOL as HOUSEHOLD_SCHOOL
    from skolekit.profile import Child as ChildConfig
    children = (ChildConfig(student_id=1, child="Beta", calendar_class_name="002", school=HOUSEHOLD_SCHOOL),
                ChildConfig(student_id=2, child="Alfa", calendar_class_name="000", school=HOUSEHOLD_SCHOOL))
    assert rc.child_classes(children=children) == {"Beta": 2, "Alfa": 0}
