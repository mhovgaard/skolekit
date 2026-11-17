import json

from skoleklar.triage.derivation import (
    DERIVATION_SCHEMA,
    PROMPT_VERSION,
    TAXONOMY_VERSION,
    derivation_manifest,
)
from skoleklar.triage.prompt_manifest import load_prompt_manifest


def test_loads_versioned_prompt_contract_from_manifest_files() -> None:
    manifest = load_prompt_manifest("derive-v9")

    assert manifest.manifest_id == "derive-v9"
    assert manifest.taxonomy_version == "school-items-v1"
    assert len(manifest.content_hash) == 64
    assert len(manifest.fewshots) == 5
    assert manifest.output_schema["properties"]["items"]["type"] == "array"
    assert json.dumps(manifest.output_schema)


def test_loads_consolidation_manifest_with_group_outputs() -> None:
    manifest = load_prompt_manifest("consolidate-v1")

    assert manifest.manifest_id == "consolidate-v1"
    assert manifest.output_schema["required"] == ["groups", "unclustered_item_ids"]
    assert manifest.fewshots[0]["output"].get("groups") is not None


def test_loads_archival_manifest_with_strict_decisions() -> None:
    manifest = load_prompt_manifest("archive-v1")

    assert manifest.manifest_id == "archive-v1"
    assert manifest.output_schema["required"] == ["decisions"]
    assert manifest.fewshots[0]["output"]["decisions"][0]["decision"] == "archive"


def test_loads_private_message_operational_fact_manifest() -> None:
    manifest = load_prompt_manifest("message-events-v2")

    assert manifest.manifest_id == "message-events-v2"
    event_kind = manifest.output_schema["properties"]["items"]["items"][
        "properties"
    ]["event_kind"]
    assert "bring" in event_kind["enum"]
    assert manifest.fewshots[1]["output"]["items"][0]["bring_items"] == [
        "syltetøjsglas",
        "papir",
    ]


def test_dashboard_manifest_requires_weekend_and_five_day_structure() -> None:
    manifest = load_prompt_manifest("dashboard-v1")
    state = manifest.output_schema["properties"]["state"]
    assert {"weekend", "weekdays"} <= set(state["required"])
    assert state["properties"]["schema_version"]["enum"] == [2]
    assert state["properties"]["weekdays"]["minItems"] == 5
    assert state["properties"]["weekdays"]["maxItems"] == 5
    assert "coming Monday" in manifest.instructions


def test_runtime_prompt_contains_semantics_and_fewshots() -> None:
    manifest = derivation_manifest()
    instructions = manifest.render_instructions()
    assert PROMPT_VERSION == manifest.manifest_id == "derive-v10"
    assert TAXONOMY_VERSION == manifest.taxonomy_version
    assert DERIVATION_SCHEMA == manifest.output_schema
    assert "# Semantic layer" in instructions
    assert "`schedule_change`" in instructions
    assert "`moves_time`" in instructions and "`recurring`" in instructions and "`category`" in instructions
    assert "# Few-shot examples" in instructions
    # The few-shots name the household's people through slots; here the invented ones.
    assert "Esther kommer gerne" in instructions and "{{" not in instructions
    assert "Expected output:\n{\n  \"items\": []" in instructions


def test_manifest_rejects_path_traversal() -> None:
    try:
        load_prompt_manifest("../derive-v9")
    except ValueError as error:
        assert "Invalid prompt manifest id" in str(error)
    else:
        raise AssertionError("path traversal should be rejected")
