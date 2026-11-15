"""Hashed per-device credentials; legacy phone access never implies refresh access."""
from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass

SCOPES = {"phone", "display", "refresh"}


@dataclass(frozen=True)
class Principal:
    device_id: str
    scopes: frozenset[str]


class DeviceAuth:
    def __init__(self, registry=None, *, legacy_hash=None, legacy_device="family-primary-iphone"):
        self.entries = []
        if registry is not None:
            if isinstance(registry, str):
                registry = json.loads(registry)
            if not isinstance(registry, list):
                raise ValueError("Device registry must be a list")
            for entry in registry:
                if set(entry) != {"device_id", "token_hash", "scopes"}:
                    raise ValueError("Invalid device registry entry")
                if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", entry["device_id"]):
                    raise ValueError("Invalid device identity")
                if not isinstance(entry["scopes"], list) or not set(entry["scopes"]) <= SCOPES:
                    raise ValueError("Invalid device scopes")
                self.entries.append((normalize_hash(entry["token_hash"]),
                                     Principal(entry["device_id"], frozenset(entry["scopes"]))))
        if legacy_hash:
            self.entries.append((normalize_hash(legacy_hash), Principal(legacy_device, frozenset({"phone"}))))
        if len({h for h, _ in self.entries}) != len(self.entries):
            raise ValueError("Device token hashes must be unique")

    def authenticate(self, authorization):
        if not authorization.startswith("Bearer ") or not 1 <= len(authorization[7:]) <= 512:
            return None
        digest = hashlib.sha256(authorization[7:].encode()).hexdigest()
        result = None
        for stored, principal in self.entries:
            if hmac.compare_digest(digest, stored):
                result = principal
        return result


def normalize_hash(value):
    if not isinstance(value, str):
        raise ValueError("Token hash must be a SHA-256 hex digest")
    value = value.removeprefix("sha256:").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("Token hash must be a SHA-256 hex digest")
    return value
