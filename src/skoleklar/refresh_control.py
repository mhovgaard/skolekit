"""Explicit refresh dispatch with durable idempotency and a fail-closed dispatch lock."""
from __future__ import annotations

import hashlib
import os
import re
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx


class Conflict(Exception):
    pass


class RefreshBusy(Exception):
    pass


class RefreshUnavailable(Exception):
    pass


def request_id(value: str) -> str:
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ValueError("request_id must be a canonical UUID")
    return value


class GCSControlStore:
    def __init__(self, bucket: str):
        from google.cloud import storage
        self.bucket = storage.Client().bucket(bucket)

    def read(self, name):
        from google.api_core.exceptions import NotFound
        import json
        blob = self.bucket.blob(name)
        try:
            blob.reload()
            generation = int(blob.generation)
            payload = blob.download_as_bytes(if_generation_match=generation)
            return json.loads(payload), generation
        except NotFound:
            return None, 0

    def put(self, name, value, generation):
        from google.api_core.exceptions import PreconditionFailed
        import json
        blob = self.bucket.blob(name)
        try:
            blob.upload_from_string(json.dumps(value), content_type="application/json",
                                    if_generation_match=generation)
            return int(blob.generation)
        except PreconditionFailed as error:
            raise Conflict() from error

    def delete(self, name, generation):
        from google.api_core.exceptions import NotFound, PreconditionFailed
        try:
            self.bucket.blob(name).delete(if_generation_match=generation)
        except (NotFound, PreconditionFailed):
            pass


class CloudRunDispatcher:
    def __init__(self, job: str):
        if not re.fullmatch(r"projects/[a-z0-9-]+/locations/[a-z0-9-]+/jobs/[a-z0-9-]+", job):
            raise ValueError("Invalid configured job")
        self.job = job

    def _call(self, method, resource):
        import google.auth
        from google.auth.transport.requests import Request
        credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        credentials.refresh(Request())
        # Never retry POST: a transport failure can mean the job was accepted.
        with httpx.Client(timeout=20) as client:
            response = client.request(method, f"https://run.googleapis.com/v2/{resource}",
                                      headers={"Authorization": f"Bearer {credentials.token}"},
                                      **({"json": {}} if method == "POST" else {}))
        response.raise_for_status()
        return response.json()

    def start(self):
        value = self._call("POST", f"{self.job}:run")
        name = value["name"]
        self._validate_operation(name)
        return name

    def _validate_operation(self, name):
        parent = self.job.split("/jobs/")[0]
        if not isinstance(name, str) or not re.fullmatch(re.escape(parent) + r"/operations/[A-Za-z0-9_-]+", name):
            raise ValueError("Invalid operation")

    def status(self, operation):
        self._validate_operation(operation)
        value = self._call("GET", operation)
        if not value.get("done"):
            return "running"
        execution = value.get("response") or {}
        failed = value.get("error") or execution.get("failedCount") or execution.get("cancelledCount")
        failed = failed or any(c.get("type") == "Completed" and c.get("state") == "CONDITION_FAILED"
                               for c in execution.get("conditions", []))
        return "failed" if failed else "complete"


class RefreshController:
    """One dispatch at a time, independent of API process count or restarts.

    An uncertain POST is never re-dispatched automatically. Its durable lock
    requires operator reconciliation against Cloud Run executions.
    """
    def __init__(self, store, dispatcher):
        self.store, self.dispatcher = store, dispatcher

    @staticmethod
    def _name(principal, identifier):
        digest = hashlib.sha256(principal.encode()).hexdigest()
        return f"requests/{digest}/{request_id(identifier)}.json"

    def _reconcile(self, name):
        record, generation = self.store.read(name)
        if record and record.get("operation") and record["status"] == "running":
            status = self.dispatcher.status(record["operation"])
            if status in {"complete", "failed"}:
                record = {**record, "status": status}
                try:
                    self.store.put(name, record, generation)
                except Conflict:
                    record, _ = self.store.read(name)
        return record

    def status(self, principal, identifier):
        record = self._reconcile(self._name(principal, identifier))
        return self._public(record) if record else None

    @staticmethod
    def _public(record):
        return {key: record[key] for key in ("request_id", "status", "requested_at")}

    def trigger(self, principal, identifier):
        name = self._name(principal, identifier)
        existing = self._reconcile(name)
        if existing:
            return self._public(existing), True
        lock, generation = self.store.read("dispatch-lock.json")
        if lock:
            previous = self._reconcile(lock["request"])
            if not previous or previous["status"] not in {"complete", "failed"}:
                raise RefreshBusy()
            self.store.delete("dispatch-lock.json", generation)
        try:
            self.store.put("dispatch-lock.json", {"request": name}, 0)
        except Conflict as error:
            raise RefreshBusy() from error
        record = {"request_id": identifier, "status": "dispatch_uncertain",
                  "requested_at": datetime.now(UTC).isoformat()}
        try:
            self.store.put(name, record, 0)
        except Conflict:
            # Another request with this ID already owns dispatch. Never start twice.
            return self.status(principal, identifier), True
        _, generation = self.store.read(name)
        try:
            operation = self.dispatcher.start()
            record = {**record, "operation": operation, "status": "running"}
            self.store.put(name, record, generation)
        except Exception as error:
            raise RefreshUnavailable() from error
        return self._public(record), False


def controller_from_environment():
    bucket, job = os.getenv("AG_CONTROL_BUCKET"), os.getenv("AG_REFRESH_JOB")
    if not bucket or not job:
        return None
    return RefreshController(GCSControlStore(bucket), CloudRunDispatcher(job))
