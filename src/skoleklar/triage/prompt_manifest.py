"""The prompt manifests, and the household's names filled into their slots.

A manifest's few-shot examples and instructions may name the household's
children and other people. The files hold slots instead (`{{child_1}}`,
`{{other_1}}`), and a manifest is filled before it is hashed, so the household's
own names give the same `content_hash` and rendered prompt as before, and every
stored result and cache key stays valid (see docs/processing.md#versioning-and-evaluation). The children's names come from
`profile.toml`, others from `triage.toml`'s `[slots]`, both in the private state
directory; without them a slot takes an invented name.
"""
from __future__ import annotations

import hashlib
import json
import re
import tomllib
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any


DEFAULT_MANIFEST_ID = "derive-v11"
_MANIFEST_ID = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
SLOT = re.compile(r"\{\{(child_[12]|other_[1-9])\}\}")
TRIAGE_FILE = "triage.toml"
# Invented names for a household that gives none.
DEFAULT_SLOTS = {"child_1": "Alfa", "child_2": "Beta", "other_1": "Clara", "other_2": "Dagny",
                 "other_3": "Esther"}


class SlotError(ValueError):
    """A slot that can't be filled, or a triage.toml that can't be used as written."""


def household_slots(home: Path | None = None) -> dict[str, str]:
    """The slots' values: the profile's children, then triage.toml's [slots], over invented names."""
    from skoleklar.household import load_site_profile, state_home

    root = state_home() if home is None else home
    slots = dict(DEFAULT_SLOTS)
    for index, child in enumerate(load_site_profile(root).children[:2], start=1):
        slots[f"child_{index}"] = child.child
    path = root / TRIAGE_FILE
    if path.exists():
        try:
            document = tomllib.loads(path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as error:
            raise SlotError(f"{TRIAGE_FILE}: {error}") from None
        if set(document) - {"slots"}:
            raise SlotError(f"{TRIAGE_FILE} holds only [slots]")
        given = document.get("slots", {})
        unknown = set(given) - set(DEFAULT_SLOTS)
        if unknown:
            raise SlotError(f"{TRIAGE_FILE}: unknown slot {sorted(unknown)[0]!r}")
        if not all(isinstance(value, str) and value.strip() for value in given.values()):
            raise SlotError(f"{TRIAGE_FILE}: each slot is a non-empty name")
        slots.update(given)
    return slots


def _fill(text: str, slots: dict[str, str]) -> str:
    return SLOT.sub(lambda match: slots[match.group(1)], text)


@dataclass(frozen=True)
class PromptManifest:
    manifest_id: str
    taxonomy_version: str
    response_schema_name: str
    prompt_cache_key: str
    instructions: str
    semantic_layer: str
    fewshots: list[dict[str, Any]]
    output_schema: dict[str, Any]
    content_hash: str

    def render_instructions(self) -> str:
        examples = []
        for index, example in enumerate(self.fewshots, start=1):
            examples.append(
                "\n".join(
                    [
                        f"Example {index}: {example['name']}",
                        "Input:",
                        json.dumps(example["input"], ensure_ascii=False, indent=2),
                        "Expected output:",
                        json.dumps(example["output"], ensure_ascii=False, indent=2),
                    ]
                )
            )
        return "\n\n".join(
            [
                self.instructions.strip(),
                "# Semantic layer\n" + self.semantic_layer.strip(),
                "# Few-shot examples\n" + "\n\n".join(examples),
            ]
        )


def load_prompt_manifest(
    manifest_id: str = DEFAULT_MANIFEST_ID, *, slots: dict[str, str] | None = None,
) -> PromptManifest:
    """The manifest with its slots filled: from `slots`, else the household's own."""
    if not _MANIFEST_ID.fullmatch(manifest_id):
        raise ValueError(f"Invalid prompt manifest id: {manifest_id!r}")
    root = files("skoleklar").joinpath("manifests", manifest_id)
    metadata = json.loads(root.joinpath("manifest.json").read_text(encoding="utf-8"))
    if metadata.get("id") != manifest_id:
        raise ValueError(
            f"Prompt manifest id mismatch: expected {manifest_id!r}, "
            f"found {metadata.get('id')!r}"
        )

    texts = {key: _read_text(root, metadata[key])
             for key in ("instructions", "semantic_layer", "fewshots", "output_schema")}
    needed = {name for text in texts.values() for name in SLOT.findall(text)}
    if needed:
        values = household_slots() if slots is None else slots
        missing = needed - values.keys()
        if missing:
            raise SlotError(f"{manifest_id}: no value for {sorted(missing)[0]}")
        texts = {key: _fill(text, values) for key, text in texts.items()}
    instructions = texts["instructions"]
    semantic_layer = texts["semantic_layer"]
    fewshots = json.loads(texts["fewshots"])
    output_schema = json.loads(texts["output_schema"])
    if not isinstance(fewshots, list) or not all(
        isinstance(value, dict)
        and {"name", "input", "output"} <= value.keys()
        and isinstance(value["output"], dict)
        for value in fewshots
    ):
        raise ValueError("Prompt manifest fewshots must contain name/input/output")
    if not isinstance(output_schema, dict):
        raise ValueError("Prompt manifest output schema must be a JSON object")

    hash_material = {
        "metadata": metadata,
        "instructions": instructions,
        "semantic_layer": semantic_layer,
        "fewshots": fewshots,
        "output_schema": output_schema,
    }
    content_hash = hashlib.sha256(
        json.dumps(
            hash_material, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    return PromptManifest(
        manifest_id=manifest_id,
        taxonomy_version=str(metadata["taxonomy_version"]),
        response_schema_name=str(metadata["response_schema_name"]),
        prompt_cache_key=str(metadata["prompt_cache_key"]),
        instructions=instructions,
        semantic_layer=semantic_layer,
        fewshots=fewshots,
        output_schema=output_schema,
        content_hash=content_hash,
    )


def _read_text(root: Any, filename: str) -> str:
    if not isinstance(filename, str) or filename != filename.rsplit("/", 1)[-1]:
        raise ValueError(f"Manifest file must be a local filename: {filename!r}")
    return root.joinpath(filename).read_text(encoding="utf-8")
