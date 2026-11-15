"""Phase 0 safety net: the whole of `collect_all` for a synthetic two-child profile.

Each golden file holds the requests `collect_all` makes against the synthetic
site, in order, and every check with every record as `SourceRecord.as_dict()`
stores it, hashes included. The school's host is written as `{BASE_URL}`.

- `daily-events-only.json` uses the cloud scan's own policy.
- `initialize-events-only.json` uses the widest one: the school year and every
  weekly plan and page. Messages are never records, so the inbox isn't read
  here; the restricted collection reads it (test_message_privacy.py).

When a change is meant to alter this output, regenerate with
`AG_UPDATE_GOLDEN=1 uv run pytest tests_python/test_collect_all_golden.py` and
review the diff like code.
"""
import json
import os
from pathlib import Path

import pytest

from skoleklar.harvest import HarvestPolicy, collect_all
from skolekit.sources import calendar as calendar_source
from tests_python.skoleintra_site import CHILDREN, SCHOOL, SyntheticSite, frozen_datetime


GOLDEN = Path(__file__).parent / "fixtures" / "collect_all"
POLICIES = {
    "daily-events-only": HarvestPolicy.for_mode(
        "daily", message_mode="events-only", message_processor="zdr"
    ),
    "initialize-events-only": HarvestPolicy.for_mode("initialize", message_mode="events-only"),
}


def harvest_document(policy: HarvestPolicy) -> dict:
    site = SyntheticSite()
    with site.client() as client:
        checks = collect_all(client, children=CHILDREN, policy=policy)
    document = {
        "children": [
            {"student_id": child.student_id, "child": child.child,
             "calendar_class_name": child.calendar_class_name}
            for child in CHILDREN
        ],
        "policy": {
            "mode": policy.mode,
            "message_mode": policy.message_mode,
            "message_processor": policy.message_processor,
        },
        "requests": [
            f"{request.method} {request.url.path}"
            + (f"?{request.url.query.decode()}" if request.url.query else "")
            + (f" {request.content.decode()}" if request.content else "")
            for request in site.requests
        ],
        "checks": [
            {
                "student_id": check.student_id,
                "child": check.child,
                "source_type": check.source_type,
                "status": check.status,
                "error": check.error,
                "complete": check.complete,
                "metadata": check.metadata,
                "coverage": check.coverage,
                "records": [record.as_dict() for record in check.records],
            }
            for check in checks
        ],
    }
    text = json.dumps(document, ensure_ascii=False, indent=1, sort_keys=False)
    return json.loads(text.replace(SCHOOL.base_url, "{BASE_URL}"))


@pytest.mark.parametrize("name", sorted(POLICIES))
def test_collect_all_output_matches_the_golden_file(monkeypatch, name: str) -> None:
    monkeypatch.setattr(calendar_source, "datetime", frozen_datetime())
    actual = harvest_document(POLICIES[name])
    path = GOLDEN / f"{name}.json"
    if os.environ.get("AG_UPDATE_GOLDEN") == "1":
        path.write_text(
            json.dumps(actual, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
        )
    expected = json.loads(path.read_text(encoding="utf-8"))

    # Compare the request log and each check apart first, for a readable failure.
    assert actual["requests"] == expected["requests"]
    assert len(actual["checks"]) == len(expected["checks"])
    for actual_check, expected_check in zip(actual["checks"], expected["checks"]):
        assert actual_check == expected_check, (
            f"{actual_check['child']}/{actual_check['source_type']} changed"
        )
    assert actual == expected


def test_golden_files_cover_both_children_and_every_source() -> None:
    for name in POLICIES:
        document = json.loads((GOLDEN / f"{name}.json").read_text(encoding="utf-8"))
        pairs = [(check["child"], check["source_type"]) for check in document["checks"]]
        assert pairs == [
            (child.child, source)
            for child in CHILDREN
            for source in ("frontpage", "messages", "calendar", "weekly_plan", "forms")
        ]
        assert all(check["status"] == "ok" for check in document["checks"])
        records = [record for check in document["checks"] for record in check["records"]]
        assert all(
            set(record) >= {"raw_hash", "content_hash", "state_hash"} for record in records
        )
        assert "{BASE_URL}" in json.dumps(document)
