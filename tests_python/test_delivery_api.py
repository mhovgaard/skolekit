import hashlib
from datetime import UTC, date, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from skoleklar.delivery_api import create_app
from skoleklar.delivery_manifest import build_delivery_manifest, publish_manifest
from skoleklar.delivery_store import LocalDeliveryStore
from tests_python.test_delivery_manifest import item


TOKEN = "local-test-device-token-with-at-least-32-bytes"
TOKEN_HASH = hashlib.sha256(TOKEN.encode()).hexdigest()
NOW = datetime(2026, 8, 25, 20, tzinfo=UTC)


def client(tmp_path: Path, *, rate_limit: int = 60) -> tuple[TestClient, LocalDeliveryStore, dict]:
    store = LocalDeliveryStore(tmp_path / "delivery")
    manifest = build_delivery_manifest(
        [item()], target_date=date(2026, 8, 25), generated_at=NOW
    )
    publish_manifest(store, manifest)
    app = create_app(
        store=store,
        token_hash=TOKEN_HASH,
        now=lambda: NOW,
        rate_limit=rate_limit,
    )
    return TestClient(app), store, manifest


def headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


def test_health_is_generic_and_unauthenticated(tmp_path: Path) -> None:
    api, _, _ = client(tmp_path)

    response = api.get("/health")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "api_version": 1}


def test_probe_is_authenticated_and_does_not_echo_token(tmp_path: Path) -> None:
    api, _, _ = client(tmp_path)
    missing = api.post(
        "/v1/probe",
        json={"device_id": "family-primary-iphone", "nonce": "missing"},
    )
    valid = api.post(
        "/v1/probe",
        headers=headers(),
        json={"device_id": "family-primary-iphone", "nonce": "test-1"},
    )

    assert missing.status_code == 401
    assert TOKEN not in missing.text
    assert valid.status_code == 200
    assert valid.json()["nonce"] == "test-1"
    assert TOKEN not in valid.text


def test_probe_accepts_api_gateway_forwarded_authorization(tmp_path: Path) -> None:
    api, _, _ = client(tmp_path)

    response = api.post(
        "/v1/probe",
        headers={
            "Authorization": "Bearer gateway-backend-identity-token",
            "X-Forwarded-Authorization": f"Bearer {TOKEN}",
        },
        json={"device_id": "family-primary-iphone", "nonce": "gateway-test"},
    )

    assert response.status_code == 200
    assert response.json()["nonce"] == "gateway-test"


def test_forwarded_authorization_cannot_fall_back_to_client_header(tmp_path: Path) -> None:
    api, _, _ = client(tmp_path)

    response = api.post(
        "/v1/probe",
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "X-Forwarded-Authorization": "Bearer invalid-forwarded-token",
        },
        json={"device_id": "family-primary-iphone", "nonce": "gateway-test"},
    )

    assert response.status_code == 401


def test_sync_returns_manifest_then_204_and_records_completion(tmp_path: Path) -> None:
    api, store, manifest = client(tmp_path)
    body = {
        "schema_version": 1,
        "device_id": "family-primary-iphone",
        "last_manifest_id": None,
        "completed_stable_keys": ["bring:rita:old:2026-08-24"],
        "observed_at": "2026-08-25T20:00:00+02:00",
    }

    first = api.post("/v1/sync", headers=headers(), json=body)
    unchanged = api.post(
        "/v1/sync",
        headers=headers(),
        json={**body, "last_manifest_id": manifest["manifest_id"]},
    )

    assert first.status_code == 200
    assert first.json() == manifest
    assert unchanged.status_code == 204
    observations = list((store.root / "completion-observations").rglob("*.json"))
    assert len(observations) == 1


def test_receipt_is_create_only_idempotent_and_manifest_scoped(tmp_path: Path) -> None:
    api, _, manifest = client(tmp_path)
    stable_key = manifest["calendar"][0]["stable_key"]
    receipt = {
        "schema_version": 1,
        "device_id": "family-primary-iphone",
        "manifest_id": manifest["manifest_id"],
        "applied_stable_keys": [stable_key],
        "failed": [],
        "finished_at": "2026-08-25T20:00:14+02:00",
    }

    accepted = api.post("/v1/receipts", headers=headers(), json=receipt)
    duplicate = api.post("/v1/receipts", headers=headers(), json=receipt)
    unknown_item = api.post(
        "/v1/receipts",
        headers=headers(),
        json={**receipt, "applied_stable_keys": ["not-in-manifest"]},
    )

    assert accepted.status_code == 202
    assert accepted.json()["duplicate"] is False
    assert duplicate.status_code == 200
    assert duplicate.json()["duplicate"] is True
    assert unknown_item.status_code == 409


def test_expired_manifest_and_rate_limit_fail_safely(tmp_path: Path) -> None:
    store = LocalDeliveryStore(tmp_path / "delivery")
    expired = build_delivery_manifest(
        [item()],
        target_date=date(2026, 8, 23),
        generated_at=datetime(2026, 8, 23, tzinfo=UTC),
    )
    publish_manifest(store, expired)
    api = TestClient(
        create_app(
            store=store,
            token_hash=TOKEN_HASH,
            now=lambda: NOW,
            rate_limit=1,
        )
    )
    body = {
        "schema_version": 1,
        "device_id": "family-primary-iphone",
        "last_manifest_id": None,
        "completed_stable_keys": [],
        "observed_at": "2026-08-25T20:00:00+02:00",
    }

    first = api.post("/v1/sync", headers=headers(), json=body)
    second = api.post("/v1/sync", headers=headers(), json=body)

    assert first.status_code == 503
    assert first.json() == {"error": "manifest_unavailable"}
    assert second.status_code == 429


def test_rejects_malformed_and_wrong_device_requests(tmp_path: Path) -> None:
    api, _, _ = client(tmp_path)

    malformed = api.post(
        "/v1/probe",
        headers=headers(),
        content=b"not-json",
    )
    wrong_device = api.post(
        "/v1/probe",
        headers=headers(),
        json={"device_id": "family-other-iphone", "nonce": "x"},
    )

    assert malformed.status_code == 400
    assert wrong_device.status_code == 401
