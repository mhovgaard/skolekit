"""Phase 0 safety net: the triage prompt manifests, byte for byte.

Every stored model result records its manifest's `content_hash`, and cache keys
are built from it, so an unintended change to a manifest file silently
re-derives facts. Since phase 8 the manifests hold slots for the household's
names, filled before hashing:

- with the owner's own values (from the owner's files, on the owner's machine)
  they give the production hashes below, exactly as before the slots;
- with the invented household every other test uses, they give the second set.

`content_hash` covers manifest.json, the instructions, the semantic layer, the
few-shots and the output schema (`prompt_manifest.load_prompt_manifest`). The
rendered hash covers the system prompt the model is sent
(`PromptManifest.render_instructions`).

A deliberate prompt change is a new manifest version, not an edit: add
`<name>-v<n+1>` and point the stage at it.
"""
import hashlib
from datetime import date

import pytest

from skoleklar.triage.prompt_manifest import load_prompt_manifest


# manifest id: (content_hash, prompt_cache_key, sha256 of render_instructions()), as in production
TRIAGE_MANIFESTS = {
    "derive-v11": (
        "5d58f5a7c5bbc68d7ff63c2cec52b6525cca03806a5e52014419779944f7f190",
        "ag-school-derive-v11",
        "763d957939706ede3ca485a1a44f49882273f75dfa3df2f32b8c529cc4f2a325",
    ),
    "message-events-v4": (
        "9683fd9093734dc6782b4b497d2e0a2da293d0c31a40e93641dc632eabf24449",
        "ag-school-message-events-v4",
        "2fd7d25ddced1b2f0c71729401c704489b7c0adf2c8807b759cf7c730f369fe6",
    ),
    "message-topic-v1": (
        "5fffed7f50af226a6c97d0dcad9d80f5dd048076c1d0a1f5d230115275bcbde7",
        "ag-school-message-topic-v1",
        "d9dd8a2bdf81967e01a30cd0dfde1f596e33cbba4d800d5d7a4dbe57b9d0ef7e",
    ),
    "archive-v1": (
        "7e68b2924308792d9ccec1d8ad91de4c7ba8f07b34bdeb754e0998b061f98423",
        "ag-school-archive-v1",
        "a8841d9b883ddf08c8f3c3025bc30037d10505ca34690149b3a5bfa25d3424d6",
    ),
    "consolidate-v1": (
        "4c76db07ef1498d22754803875f7c5b272f85485e9dee7425b036a56c5c85c7e",
        "ag-school-consolidate-v1",
        "cfeb2b8ee6385c08e45a8a82261e65b98bdd4fa86e7e1277098049b1d94fbb98",
    ),
    "review-check-v1": (
        "6ef91b449699fd08d0e20e5fe05ee35c12145a8f56ced0db4055335479d4046b",
        "ag-school-review-check-v1",
        "8f42fa60b0d09c2a72ab9e6b1df106b579b04895585dbb601e0699f1f135ca6a",
    ),
}


# manifest id: (content_hash, sha256 of render_instructions()) for the invented household
INVENTED = {
    "derive-v11": (
        "d95103d2d1d1c6d4930a77b7142a903e8828fc88a062df73f78dc955c0a45360",
        "20a4ea41f5d6e11d7e299407ca78cd3b842631b7983f7aa185e99f529917fad7",
    ),
    "message-events-v4": (
        "164ef04868e71dc1938472dd142cf64980b831c7f1bf5928e95c42d2a38c6cff",
        "3f1b85061dd989158b2c05ffb57bf9643e6d69f7c5922beb4c54fa22f8c32136",
    ),
    "message-topic-v1": (
        "62a642273288b6c87cddeea36bb03644e72b6c0d2f655538e87bb3bcbd023cd4",
        "065b14276b2477b94dcd19a0cc512d2f1017cef09ca773f36113a355315257cd",
    ),
    "archive-v1": (
        "4efa045242b12a3c64b6caf9b270efd0bc5c2f38621b492692e892673f5cacef",
        "ad6fb3a481ad5786ecf07c06357a1d748d89734dd116e9f80d0dc89b060befb5",
    ),
    "consolidate-v1": (
        "1b2b17501de6d9cfee063da00bc77d1bf1c22f6aebb054ed424cecde358617e1",
        "f833d75343e486cd2db2cf6137695c2378f61fd3f95b031bdbbf2e40d900caa5",
    ),
    "review-check-v1": (
        "80faf13813c156248263a15eab00faf15a691e3a242dd972fb97a2d24b800b66",
        "821eff52c856af9dd7a95f5763fce9a43be4179d59794ea51fefc8062ec6e30e",
    ),
}


def rendered(manifest) -> str:
    return hashlib.sha256(manifest.render_instructions().encode("utf-8")).hexdigest()


@pytest.mark.parametrize("manifest_id", sorted(TRIAGE_MANIFESTS))
def test_the_households_own_names_give_the_production_prompts(manifest_id: str) -> None:
    # The owner's values, read from the owner's files: never written into the repository.
    from skoleklar.triage.prompt_manifest import household_slots
    from tests_python.household_guard import OWNER_HOME, household_patterns

    if household_patterns() is None or not (OWNER_HOME / "triage.toml").exists():
        pytest.skip("the owner's files are not on this machine")
    manifest = load_prompt_manifest(manifest_id, slots=household_slots(OWNER_HOME))
    content_hash, cache_key, rendered_hash = TRIAGE_MANIFESTS[manifest_id]
    assert (manifest.content_hash, manifest.prompt_cache_key, rendered(manifest)) == (
        content_hash, cache_key, rendered_hash)


@pytest.mark.parametrize("manifest_id", sorted(INVENTED))
def test_the_invented_household_gives_these_prompts(manifest_id: str) -> None:
    manifest = load_prompt_manifest(manifest_id)
    assert (manifest.content_hash, rendered(manifest)) == INVENTED[manifest_id]
    assert manifest.prompt_cache_key == TRIAGE_MANIFESTS[manifest_id][1]


def test_a_slot_without_a_value_or_a_bad_triage_file_is_refused(tmp_path) -> None:
    from skoleklar.triage.prompt_manifest import SlotError, household_slots
    from tests_python.synthetic_household import write_household

    with pytest.raises(SlotError, match="no value for child_1"):
        load_prompt_manifest("derive-v11", slots={})
    home = write_household(tmp_path)
    (home / "triage.toml").write_text('[slots]\nother_1 = "Clara"\nfriend = "X"\n', encoding="utf-8")
    with pytest.raises(SlotError, match="unknown slot 'friend'"):
        household_slots(home)
    (home / "triage.toml").write_text('[slots]\nother_1 = "Ida"\n', encoding="utf-8")
    assert household_slots(home)["other_1"] == "Ida" and household_slots(home)["child_1"] == "Alfa"


class RecordingClient:
    def __init__(self, output):
        self.output = output
        self.manifests = []

    def generate(self, document, *, manifest, **kwargs):
        self.manifests.append(manifest)
        return {"output": self.output}


def test_each_triage_stage_uses_its_pinned_manifest() -> None:
    """The stage-to-manifest wiring the household's triage.toml will reproduce."""
    from skoleklar.triage import archival, consolidation, derivation, message_events, message_topics
    from skoleklar.triage.review_checks import model_verdicts

    loaded = {
        "message-facts": message_events.message_event_manifest(),
        "items": derivation.derivation_manifest(),
        "archive": archival.archival_manifest(),
        "consolidate": consolidation.consolidation_manifest(),
    }
    topics = RecordingClient({"topics": []})
    message_topics.topic_writer(topics)(["Synthetic subject"], ["Alfa"])
    loaded["message-topic"] = topics.manifests[0]
    review = RecordingClient({"verdicts": []})
    item = {"stable_key": "synthetic-1", "child": "Alfa", "title": "Tur", "effective_date": "2026-11-18"}
    model_verdicts(review, date(2026, 11, 12), {"Alfa": 1}, [item], [item])
    loaded["review"] = review.manifests[0]

    assert {stage: manifest.manifest_id for stage, manifest in loaded.items()} == {
        "message-facts": "message-events-v4",
        "message-topic": "message-topic-v1",
        "items": "derive-v11",
        "archive": "archive-v1",
        "consolidate": "consolidate-v1",
        "review": "review-check-v1",
    }
    for manifest in loaded.values():
        assert manifest.content_hash == INVENTED[manifest.manifest_id][0]
