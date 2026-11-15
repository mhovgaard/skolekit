from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from collections import defaultdict, deque
from collections.abc import Callable
from datetime import UTC, datetime
from time import monotonic
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from skoleklar.contracts import validate_contract
from skoleklar.device_auth import DeviceAuth
from skoleklar.refresh_control import (controller_from_environment, request_id, RefreshBusy, RefreshUnavailable)
from starlette.concurrency import run_in_threadpool
from skoleklar.delivery_store import (
    DeliveryObjectExistsError,
    DeliveryObjectNotFoundError,
    DeliveryStore,
    delivery_store_from_environment,
)
from skolekit.records import canonical_hash


MAX_REQUEST_BYTES = 64 * 1024
DEFAULT_DEVICE_ID = "family-primary-iphone"
SAFE_UNAUTHORIZED = {"error": "unauthorized"}
logger = logging.getLogger("agskoleintra.delivery")


class FixedWindowLimiter:
    def __init__(self, *, limit: int = 60, window_seconds: float = 60.0):
        self.limit = limit
        self.window_seconds = window_seconds
        self.requests: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = monotonic()
        values = self.requests[key]
        while values and values[0] <= now - self.window_seconds:
            values.popleft()
        if len(values) >= self.limit:
            return False
        values.append(now)
        return True


def create_app(
    *,
    store: DeliveryStore | None = None,
    token_hash: str | None = None,
    device_id: str | None = None,
    now: Callable[[], datetime] | None = None,
    rate_limit: int = 60,
    device_registry=None,
    refresh_controller=None,
    display_store=None,
    status_store=None,
) -> FastAPI:
    delivery_store = store or delivery_store_from_environment()
    expected_hash = _normalized_token_hash(
        token_hash if token_hash is not None else os.environ.get("AG_DEVICE_TOKEN_HASH")
    )
    expected_device = device_id or os.environ.get("AG_DEVICE_ID", DEFAULT_DEVICE_ID)
    auth = DeviceAuth(device_registry if device_registry is not None else os.getenv("AG_DEVICE_REGISTRY"),
                      legacy_hash=expected_hash, legacy_device=expected_device)
    refresh = refresh_controller or controller_from_environment()
    clock = now or (lambda: datetime.now(UTC))
    limiter = FixedWindowLimiter(limit=rate_limit)
    app = FastAPI(
        title="Skoleklar delivery API",
        version="1",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    def event(name: str, **fields: Any) -> None:
        logger.info(json.dumps({"event": name, **fields}, separators=(",", ":")))

    def authorize(request: Request, scope: str):
        authorization = request.headers.get("x-forwarded-authorization",
                                             request.headers.get("authorization", ""))
        principal = auth.authenticate(authorization)
        if principal is None:
            return None, JSONResponse(SAFE_UNAUTHORIZED, status_code=401)
        if scope not in principal.scopes:
            return None, JSONResponse({"error": "forbidden"}, status_code=403)
        if not limiter.allow(principal.device_id):
            return None, JSONResponse({"error": "rate_limited"}, status_code=429)
        return principal, None

    async def read_json(request: Request):
        content_length = request.headers.get("content-length")
        if content_length and content_length.isdigit() and int(content_length) > MAX_REQUEST_BYTES:
            return None, JSONResponse({"error": "request_too_large"}, status_code=413)
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > MAX_REQUEST_BYTES:
                return None, JSONResponse({"error": "request_too_large"}, status_code=413)
        try:
            value = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None, JSONResponse({"error": "invalid_json"}, status_code=400)
        if not isinstance(value, dict):
            return None, JSONResponse({"error": "invalid_request"}, status_code=400)
        return value, None

    async def authorized_json(request: Request, contract: str):
        principal, error = authorize(request, "phone")
        if error is not None:
            return None, error
        value, error = await read_json(request)
        if error is not None:
            return None, error
        try:
            validate_contract(contract, value)
        except ValueError:
            return None, JSONResponse({"error": "invalid_request"}, status_code=400)
        if value["device_id"] != principal.device_id:
            event("device_rejected", path=request.url.path)
            return None, JSONResponse(SAFE_UNAUTHORIZED, status_code=401)
        return value, None

    @app.middleware("http")
    async def private_responses(request: Request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.post("/v1/refresh")
    async def trigger_refresh(request: Request):
        principal, error = authorize(request, "refresh")
        if error is not None:
            return error
        value, error = await read_json(request)
        if error is not None:
            return error
        try:
            if set(value) != {"request_id"}:
                raise ValueError()
            identifier = request_id(value["request_id"])
        except (ValueError, TypeError, AttributeError):
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        if refresh is None:
            return JSONResponse({"error": "refresh_unavailable"}, status_code=503)
        try:
            result, duplicate = await run_in_threadpool(refresh.trigger, principal.device_id, identifier)
        except RefreshBusy:
            return JSONResponse({"error": "refresh_in_progress"}, status_code=409)
        except Exception:
            return JSONResponse({"error": "refresh_dispatch_uncertain", "request_id": identifier}, status_code=503)
        return JSONResponse({**result, "duplicate": duplicate}, status_code=200 if duplicate else 202)

    @app.get("/v1/refresh/{identifier}")
    async def refresh_status(identifier: str, request: Request):
        principal, error = authorize(request, "refresh")
        if error is not None:
            return error
        try:
            request_id(identifier)
        except (ValueError, TypeError, AttributeError):
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        if refresh is None:
            return JSONResponse({"error": "refresh_unavailable"}, status_code=503)
        try:
            result = await run_in_threadpool(refresh.status, principal.device_id, identifier)
        except Exception:
            return JSONResponse({"error": "status_unavailable"}, status_code=503)
        return JSONResponse(result or {"error": "not_found"}, status_code=200 if result else 404)

    from skoleklar.display_store import (
        VersionChanged,
        current_bundle,
        display_store_from_environment,
        panel_frame_bytes,
        png_bytes,
    )
    displays = display_store or display_store_from_environment()

    @app.get("/v1/display/status")
    async def display_status(request: Request):
        principal, error = authorize(request, "display")
        if error is not None:
            return error
        metadata = None
        upcoming = []
        try:
            bundle = await run_in_threadpool(current_bundle, displays, now=clock())
            # Technical publication metadata only; never return rendered family
            # content, credentials, job controls, or expired publication details.
            keys = ("version", "generated_at", "source_updated_at", "expires_at", "status",
                    "width", "height", "frame_bytes", "frame_format", "frame_sha256")
            metadata = {key: bundle["metadata"][key] for key in keys}
            # A timeline frame also says when it gives way to the next one.
            for key in ("valid_from", "valid_until"):
                if key in bundle["metadata"]:
                    metadata[key] = bundle["metadata"][key]
            upcoming = bundle["upcoming"]
        except Exception:
            pass
        return JSONResponse({"ok": True, "api_version": 1, "device_id": principal.device_id,
                             "server_time": _timestamp(clock()),
                             "publication_available": metadata is not None, "metadata": metadata,
                             "next_change_at": upcoming[0]["valid_from"] if upcoming else None,
                             "upcoming": upcoming})

    @app.get("/v1/display/current")
    async def display_current(request: Request):
        _, error = authorize(request, "display")
        if error is not None:
            return error
        try:
            bundle = await run_in_threadpool(current_bundle, displays, now=clock())
            return JSONResponse(bundle["metadata"])
        except Exception:
            return JSONResponse({"error": "display_unavailable"}, status_code=503)

    @app.get("/v1/display/image")
    async def display_image(request: Request):
        _, error = authorize(request, "display")
        if error is not None:
            return error
        try:
            bundle = await run_in_threadpool(current_bundle, displays, now=clock(),
                                             version=request.query_params.get("version"))
            metadata = bundle["metadata"]
            etag = '"' + metadata["sha256"] + '"'
            if request.headers.get("if-none-match") == etag:
                return Response(status_code=304, headers={"ETag": etag})
            return Response(png_bytes(bundle), media_type="image/png",
                            headers={"ETag": etag, "X-Display-Version": metadata["version"]})
        except VersionChanged:
            return JSONResponse({"error": "version_changed"}, status_code=409)
        except Exception:
            return JSONResponse({"error": "display_unavailable"}, status_code=503)

    @app.get("/v1/display/frame")
    async def display_frame(request: Request):
        _, error = authorize(request, "display")
        if error is not None:
            return error
        try:
            bundle = await run_in_threadpool(current_bundle, displays, now=clock(),
                                             version=request.query_params.get("version"))
            metadata = bundle["metadata"]
            etag = '"' + metadata["frame_sha256"] + '"'
            if request.headers.get("if-none-match") == etag:
                return Response(status_code=304, headers={"ETag": etag})
            return Response(
                await run_in_threadpool(panel_frame_bytes, bundle),
                media_type="application/octet-stream",
                headers={"ETag": etag, "X-Display-Version": metadata["version"],
                         "X-Frame-Format": metadata["frame_format"]},
            )
        except VersionChanged:
            return JSONResponse({"error": "version_changed"}, status_code=409)
        except Exception:
            return JSONResponse({"error": "display_unavailable"}, status_code=503)

    @app.post("/v1/display/receipt")
    async def display_receipt(request: Request):
        principal, error = authorize(request, "display")
        if error is not None:
            return error
        value, error = await read_json(request)
        if error is not None:
            return error
        import re
        if (set(value) != {"version", "result"} or not isinstance(value["result"], str)
                or value["result"] not in {"displayed", "failed"}
                or not isinstance(value["version"], str)
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value["version"])):
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        receipt = {**value, "device_id": principal.device_id, "received_at": _timestamp(clock())}
        # Deliberately outside phone receipts: never affects school workflow.
        name = f"display-receipts/{principal.device_id}/{canonical_hash(receipt)}.json"
        await run_in_threadpool(delivery_store.write_json, name, receipt)
        return JSONResponse({"ok": True}, status_code=202)

    from skoleklar.board_status import sanitize_report, status_object, status_store_from_environment
    statuses = status_store if status_store is not None else status_store_from_environment()

    @app.post("/v1/display/device-status")
    async def device_status(request: Request):
        """The board's own status report; only the latest one per device is kept."""
        principal, error = authorize(request, "display")
        if error is not None:
            return error
        value, error = await read_json(request)
        if error is not None:
            return error
        try:
            report = sanitize_report(value)
        except ValueError:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        if statuses is None:
            return JSONResponse({"error": "status_unavailable"}, status_code=503)
        record = {"schema_version": 1, "device_id": principal.device_id,
                  "received_at": _timestamp(clock()), "report": report}
        try:
            await run_in_threadpool(statuses.write_json, status_object(principal.device_id), record)
        except Exception:
            return JSONResponse({"error": "status_unavailable"}, status_code=503)
        event("device_status_received", device_id=principal.device_id)
        return JSONResponse({"ok": True, "received_at": record["received_at"]}, status_code=202)

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"ok": True, "api_version": 1}

    @app.post("/v1/probe")
    async def probe(request: Request) -> Response:
        value, error = await authorized_json(request, "probe-request-v1")
        if error is not None:
            return error
        assert value is not None
        event("probe_succeeded", device_id=value["device_id"])
        return JSONResponse(
            {
                "ok": True,
                "api_version": 1,
                "device_id": value["device_id"],
                "nonce": value["nonce"],
                "server_time": _timestamp(clock()),
            }
        )

    @app.post("/v1/sync")
    async def sync(request: Request) -> Response:
        value, error = await authorized_json(request, "sync-request-v1")
        if error is not None:
            return error
        assert value is not None
        if value["completed_stable_keys"]:
            observation = {
                "schema_version": 1,
                "device_id": value["device_id"],
                "completed_stable_keys": value["completed_stable_keys"],
                "observed_at": value["observed_at"],
                "received_at": _timestamp(clock()),
            }
            observation_hash = canonical_hash(observation)
            name = (
                f"completion-observations/{value['device_id']}/"
                f"{observation_hash}.json"
            )
            try:
                delivery_store.write_json(name, observation, create_only=True)
            except DeliveryObjectExistsError:
                pass
        try:
            pointer = delivery_store.read_json("current.json")
            manifest = delivery_store.read_json(pointer["object"])
            validate_contract("manifest-v1", manifest)
        except (DeliveryObjectNotFoundError, KeyError, TypeError, ValueError):
            event("manifest_unavailable", device_id=value["device_id"])
            return JSONResponse({"error": "manifest_unavailable"}, status_code=503)
        if _parse_timestamp(manifest["expires_at"]) <= clock().astimezone(UTC):
            event("manifest_expired", device_id=value["device_id"])
            return JSONResponse({"error": "manifest_unavailable"}, status_code=503)
        if value["last_manifest_id"] == manifest["manifest_id"]:
            event("manifest_unchanged", device_id=value["device_id"])
            return Response(status_code=204)
        event("manifest_served", device_id=value["device_id"], manifest_id=manifest["manifest_id"])
        return JSONResponse(manifest)

    @app.post("/v1/receipts")
    async def receipts(request: Request) -> Response:
        value, error = await authorized_json(request, "receipt-v1")
        if error is not None:
            return error
        assert value is not None
        manifest_name = f"manifests/{value['manifest_id']}.json"
        try:
            manifest = delivery_store.read_json(manifest_name)
            validate_contract("manifest-v1", manifest)
        except (DeliveryObjectNotFoundError, ValueError):
            return JSONResponse({"error": "unknown_manifest"}, status_code=409)
        known_keys = {
            item["stable_key"]
            for section in ("calendar", "reminders", "review")
            for item in manifest[section]
        }
        applied = set(value["applied_stable_keys"])
        failed = {item["stable_key"] for item in value["failed"]}
        if applied & failed or not ((applied | failed) <= known_keys):
            return JSONResponse({"error": "invalid_receipt_items"}, status_code=409)
        receipt_hash = canonical_hash(value)
        name = (
            f"receipts/{value['device_id']}/{value['manifest_id']}/"
            f"{receipt_hash}.json"
        )
        duplicate = False
        try:
            delivery_store.write_json(name, value, create_only=True)
        except DeliveryObjectExistsError:
            duplicate = True
        event(
            "receipt_accepted",
            device_id=value["device_id"],
            manifest_id=value["manifest_id"],
            duplicate=duplicate,
        )
        return JSONResponse(
            {"ok": True, "receipt_id": receipt_hash, "duplicate": duplicate},
            status_code=200 if duplicate else 202,
        )

    return app


def _normalized_token_hash(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.removeprefix("sha256:").strip().lower()
    if len(normalized) != 64 or any(character not in "0123456789abcdef" for character in normalized):
        raise ValueError("AG_DEVICE_TOKEN_HASH must be a SHA-256 hex digest")
    return normalized


def _authorization_hash(value: str) -> str | None:
    if not value.startswith("Bearer "):
        return None
    token = value[7:]
    if not token or len(token) > 512:
        return None
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("Clock must return a timezone-aware datetime")
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


app = create_app()
