from __future__ import annotations

import json
from functools import lru_cache
from importlib.resources import files
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker


CONTRACT_NAMES = {
    "manifest-v1",
    "probe-request-v1",
    "receipt-v1",
    "sync-request-v1",
}


@lru_cache(maxsize=None)
def contract_schema(name: str) -> dict[str, Any]:
    if name not in CONTRACT_NAMES:
        raise ValueError(f"Unknown delivery contract: {name}")
    resource = files("skoleklar").joinpath("contracts", f"{name}.schema.json")
    return json.loads(resource.read_text(encoding="utf-8"))


@lru_cache(maxsize=None)
def contract_validator(name: str) -> Draft202012Validator:
    schema = contract_schema(name)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def validate_contract(name: str, value: Any) -> None:
    errors = sorted(
        contract_validator(name).iter_errors(value),
        key=lambda error: [str(part) for part in error.absolute_path],
    )
    if not errors:
        return
    error = errors[0]
    location = ".".join(str(part) for part in error.absolute_path) or "$"
    raise ValueError(f"{name} validation failed at {location}: {error.message}")
