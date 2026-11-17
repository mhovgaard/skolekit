"""The one fact model (skoleklar.fact): a field added to it reaches every stage.

The plan's gate for phase 2: a test that adds a field touches only the schema and
the extractor. Here the field is added to `fact.MEANING` alone; the extractor's
answer carries it, and the store, consolidation, the brief, the board's context
and the week view pass it on without another edit. Synthetic data only.
"""
from __future__ import annotations

from datetime import date, datetime

import pytest

from skoleklar import fact as fact_model
from skoleklar.brief import build_brief_range
from skoleklar.dashboard_context import export_context
from skoleklar.storage import SnapshotStore
from skoleklar.triage.consolidation import materialize_consolidation
from skoleklar.triage.derivation import validate_items
from skoleklar.week import week_view
from skolekit.records import SourceCheck, SourceRecord

QUIET = fact_model.Field("quiet_day", bool, frozenset({"event"}), False, "any")


@pytest.fixture
def with_a_new_field(monkeypatch):
    """The schema change, and the only one: a new meaning field."""
    monkeypatch.setattr(fact_model, "MEANING", (*fact_model.MEANING, QUIET))


def extractor_answer(**fields):
    """An item as the extractor (derive-v11 plus the new field) returns it."""
    return {"dedupe_hint": "skovdag", "stream": "calendar", "type": "event", "category": "trip",
            "moves_time": None, "recurring": False, "school_subject": None, "teacher": None,
            "bring_items": [], "rain_gear": False, "audience": "child", "child": "ChildA",
            "title": "Skovdag", "summary": "Klassen er i skoven.", "effective_date": "2026-10-27",
            "end_date": None, "all_day": True, "timing_rule": "explicit", "actionability": "informational",
            "urgency": "routine", "urgency_reason": "", "confidence": 0.9, "needs_review": False,
            "review_reason": "", "evidence": [{"quote": "Skovdag", "source_day": None, "subject": None}],
            **fields}


def stored_facts(tmp_path, answers):
    """The extractor's answers validated and stored, as `pipeline.derive` does."""
    path = tmp_path / "facts.db"
    with SnapshotStore(path) as store:
        run = store.begin_run()
        notice = SourceRecord(student_id=1, child="ChildA", source_type="frontpage", source_key="notice:1",
                              source_url="https://example.invalid/notice/1", title="Skovdag",
                              body_text="Skovdag.", source_data={}, content_data={}, raw_payload={})
        store.store_checks(run, [SourceCheck(student_id=index, child=child, source_type="frontpage",
                                             records=[notice] if child == "ChildA" else [], complete=True)
                                 for index, child in enumerate(("ChildA", "ChildB"), 1)])
        [candidate] = store.derivation_candidates(run)
        document = {"title": "Skovdag", "body_text": "Skovdag."}
        items = validate_items(answers, candidate=candidate, document=document)
        derivation = store.begin_derivation(harvest_run_id=run, cache_key="c", provider="test", model="m",
                                            prompt_version="derive-v11", taxonomy_version="t")
        store.complete_derivation(derivation, items=items, sources=candidate["sources"], response_id="r",
                                  usage={})
    return path


def test_a_field_added_to_the_model_reaches_every_stage(tmp_path, with_a_new_field):
    path = stored_facts(tmp_path, [extractor_answer(quiet_day=True)])
    with SnapshotStore(path, read_only=True) as store:
        # The store.
        [stored] = store.current_derived_items()
        assert stored["quiet_day"] is True and fact_model.current(stored)
        # Consolidation, for an unchanged item and a merged one.
        [unchanged] = materialize_consolidation({"groups": [], "unclustered_item_ids": [stored["id"]]},
                                                items=[stored])
        assert unchanged["quiet_day"] is True
        twin = {**stored, "id": stored["id"] + 1, "stable_key": "twin", "quiet_day": False}
        [merged] = materialize_consolidation({"groups": [{
            "member_item_ids": [stored["id"], twin["id"]], "resolution": "duplicate", "needs_review": False,
            "review_reason": "", "reason": "Same trip.",
            "canonical": {key: stored[key] for key in (
                "child", "stream", "audience", "title", "summary", "effective_date", "end_date", "all_day",
                "timing_rule", "actionability", "urgency", "urgency_reason", "confidence", "needs_review",
                "review_reason")} | {"type": "event"}}], "unclustered_item_ids": []},
            items=[stored, twin])
        assert merged["quiet_day"] is True  # `any`, as the field's merge rule says
        # The brief.
        brief = build_brief_range([stored], start_date=date(2026, 10, 26), end_date=date(2026, 11, 2))
        assert brief["items"][0]["quiet_day"] is True
        # The board's context.
        context = export_context(store, now=datetime.fromisoformat("2026-10-26T19:30:00+01:00"))
        assert context["items"][0]["quiet_day"] is True
        assert context["contract"] == {"current": fact_model.CONTRACT, "stale": 0}
        # The week view, which the MCP serves.
        week = week_view(store, start=date(2026, 10, 26))
        assert week["facts"][0]["quiet_day"] is True


def test_the_new_field_is_kept_only_where_it_means_something(with_a_new_field):
    homework = fact_model.normalize({"type": "homework", "quiet_day": True})
    event = fact_model.normalize({"type": "event", "quiet_day": "yes"})
    assert homework["quiet_day"] is False and event["quiet_day"] is False


def test_every_field_has_one_definition():
    names = fact_model.names()
    assert len(names) == len(set(names))
    for field in fact_model.MEANING:
        assert field.merge in {"agree", "any", "all", "union"}
        assert field.normal(None, next(iter(field.types))) == field.empty


@pytest.mark.parametrize("stage", ["store/facts.py", "triage/consolidation.py", "brief.py",
                                   "dashboard_context.py", "week.py", "mcp/server.py",
                                   "delivery_manifest.py", "present.py", "parents_board.py",
                                   "dashboard_rules.py", "dashboard_build.py"])
def test_no_stage_keeps_its_own_list_of_meaning_fields(stage):
    """A stage may read a field it uses; it never lists the fields it copies. (The restricted
    message extractor's privacy allowlist in store/message_facts.py is the extractor's own.)"""
    import ast
    from pathlib import Path

    import skoleklar

    tree = ast.parse((Path(skoleklar.__file__).parent / stage).read_text())
    meaning = set(fact_model.names())
    for node in ast.walk(tree):
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            listed = {element.value for element in node.elts
                      if isinstance(element, ast.Constant) and element.value in meaning}
            assert len(listed) < 2, (stage, sorted(listed))


# --- Migrate, don't fall back ----------------------------------------------------------------

def stored_under_an_older_contract(tmp_path):
    """A notice whose live facts were made by derive-v10, seen again unchanged in a later run."""
    path = tmp_path / "facts.db"
    notice = SourceRecord(student_id=1, child="ChildA", source_type="frontpage", source_key="notice:1",
                          source_url="https://example.invalid/notice/1", title="Skovdag",
                          body_text="Skovdag.", source_data={}, content_data={}, raw_payload={})
    checks = [SourceCheck(student_id=index, child=child, source_type="frontpage",
                          records=[notice] if child == "ChildA" else [], complete=True)
              for index, child in enumerate(("ChildA", "ChildB"), 1)]
    with SnapshotStore(path) as store:
        run = store.begin_run()
        store.store_checks(run, checks)
        [candidate] = store.derivation_candidates(run)
        old = extractor_answer() | {"stable_key": "older-key", "effective_date": "2026-11-16"}
        for key in (*fact_model.names(), "contract"):
            old.pop(key, None)
        derivation = store.begin_derivation(harvest_run_id=run, cache_key="old", provider="test", model="m",
                                            prompt_version="derive-v10", taxonomy_version="t")
        store.complete_derivation(derivation, items=[old], sources=candidate["sources"], response_id="r",
                                  usage={})
        store.store_checks(store.begin_run(), checks)
    return path


def test_the_window_starts_at_the_monday_of_the_shown_week():
    from skoleklar.pipeline import _window_start
    # Tuesday and Friday: the board shows this week, from Monday.
    assert _window_start(date(2026, 11, 17)) == _window_start(date(2026, 11, 20)) == date(2026, 11, 16)
    # Saturday: the board shows next week, so the window starts today.
    assert _window_start(date(2026, 11, 21)) == date(2026, 11, 21)


def test_a_source_on_an_older_contract_earlier_in_the_shown_week_is_rederived(tmp_path):
    from skoleklar.pipeline import MIGRATION_DAYS, _plan_keys, _window_start
    from skoleklar.triage.derivation import PROMPT_VERSION, ROUTINE_CALENDAR_FILTER_VERSION

    path = stored_under_an_older_contract(tmp_path)
    with SnapshotStore(path) as store:
        assert store.derivation_candidates() == []  # unchanged: only the migration finds it
        as_of = date(2026, 11, 18)  # Wednesday; the fact is Monday's
        found = store.contract_candidates(
            current_versions={PROMPT_VERSION, ROUTINE_CALENDAR_FILTER_VERSION}, start=_window_start(as_of),
            end=date(2026, 12, 6), weekly_plan_keys=_plan_keys(as_of), limit=MIGRATION_DAYS)
        assert [candidate["source_key"] for candidate in found] == ["notice:1"]
        assert found[0]["change"] == "contract"


def test_a_failed_rederivation_never_stops_the_scan(tmp_path, monkeypatch):
    from skoleklar import pipeline

    calls = []

    class Failing:
        def __init__(self, **_):
            pass

        def derive(self, document):
            calls.append(document)
            raise RuntimeError("the model is down")

    path = stored_under_an_older_contract(tmp_path)
    monkeypatch.setattr(pipeline, "openrouter_api_key", lambda: "synthetic-key")
    monkeypatch.setattr(pipeline, "OpenRouterClient", Failing)
    result = pipeline.derive(pipeline.DeriveRequest(db=path, migrate=True, as_of="2026-11-18"))
    # The source keeps its older facts, the stage succeeds, and the count says what failed.
    assert result.exit_code == 0
    assert (result.output["contract_candidate_count"], result.output["contract_failure_count"],
            result.output["failures"]) == (1, 1, 0)
    assert result.output["continuity"] == {"kept_count": 0, "lost_count": 0, "lost": []}
    # The next scan the same day backs off: no model call, and the source still counts.
    again = pipeline.derive(pipeline.DeriveRequest(db=path, migrate=True, as_of="2026-11-18"))
    assert again.exit_code == 0 and len(calls) == 1
    assert (again.output["contract_backoff_count"], again.output["contract_failure_count"]) == (1, 0)
    # A day later it is tried again.
    monkeypatch.setattr(pipeline, "_failed_recently", lambda completed_at: False)
    pipeline.derive(pipeline.DeriveRequest(db=path, migrate=True, as_of="2026-11-18"))
    assert len(calls) == 2


def test_the_scan_logs_why_a_rederivation_failed_by_code_only():
    from skoleklar.runtime.scan import _stage_summary
    summary = _stage_summary("derive", {
        "candidate_count": 3, "contract_candidate_count": 2, "contract_failure_count": 1,
        "contract_backoff_count": 1, "failures": 0,
        "results": [{"status": "failed", "contract": True,
                     "error": "OpenRouter Chat Completions API returned 429: private detail"}]})
    assert summary == {"candidate_count": 3, "contract_candidate_count": 2, "contract_failure_count": 1,
                       "failures": 0, "contract_backoff_count": 1, "contract_failure_code": "provider_http_429"}
