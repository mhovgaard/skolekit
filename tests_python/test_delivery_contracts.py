import json
from pathlib import Path

import pytest

from skoleklar.contracts import validate_contract


FIXTURES = Path(__file__).parent / "fixtures" / "delivery"


@pytest.mark.parametrize(
    "name",
    [
        "manifest-create.json",
        "manifest-update.json",
        "manifest-cancellation.json",
        "manifest-ambiguous.json",
        "manifest-expired.json",
    ],
)
def test_valid_manifest_fixtures(name: str) -> None:
    value = json.loads((FIXTURES / name).read_text(encoding="utf-8"))

    validate_contract("manifest-v1", value)


def test_valid_request_and_receipt_fixtures() -> None:
    validate_contract(
        "receipt-v1",
        json.loads((FIXTURES / "receipt-success.json").read_text(encoding="utf-8")),
    )
    validate_contract(
        "sync-request-v1",
        json.loads((FIXTURES / "sync-completion.json").read_text(encoding="utf-8")),
    )


def test_malformed_manifest_fixture_is_rejected() -> None:
    value = json.loads((FIXTURES / "manifest-malformed.json").read_text(encoding="utf-8"))

    with pytest.raises(ValueError, match="manifest-v1 validation failed"):
        validate_contract("manifest-v1", value)
