"""The GCP runtime: a scan in a Cloud Run job, with its state in the private bucket.

What the cloud job did before phase 10, behind the runtime's methods:

- the database and the session are downloaded from the private bucket and
  uploaded back with a generation match, so a second writer fails the upload;
- the phone manifest goes to the delivery bucket, the board's frames to the
  display bucket, the run summary and the lock to the control bucket;
- the settings come from the job's environment.
"""
from __future__ import annotations

import contextlib
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from google.api_core.exceptions import NotFound, PreconditionFailed
from google.cloud import storage

from skolekit.profile import CONFIG_FILES
from skoleklar.delivery_store import GCSDeliveryStore
from skoleklar.meaning import MEANING_FILE
from skoleklar.restricted_messages import (
    resolve_message_mode,
    resolve_message_processor,
)
from skoleklar.runtime.scan import DATABASE, SESSION, ScanSettings
from skoleklar.triage.derivation import DEFAULT_MODEL
from skoleklar.triage.prompt_manifest import TRIAGE_FILE
from skoleklar.weekend_art import ARTWORK_FOLDER, PRESETS

# The database lives under its own prefix; the shared session sits beside the profile files.
RESTRICTED_STATE_OBJECTS = (DATABASE,)
RESTRICTED_PREFIX = "state/restricted-v1"


def download_state(bucket: Any, state_dir: Path, *, prefix: str = "state",
                   filenames: tuple[str, ...]) -> dict[str, int | None]:
    generations: dict[str, int | None] = {}
    for filename in filenames:
        blob = bucket.blob(f"{prefix}/{filename}")
        try:
            blob.reload()
            blob.download_to_filename(state_dir / filename)
            generations[filename] = int(blob.generation)
        except NotFound:
            generations[filename] = None
    return generations


def upload_state(bucket: Any, state_dir: Path, generations: dict[str, int | None], *,
                 prefix: str = "state", filenames: tuple[str, ...]) -> None:
    for filename in filenames:
        path = state_dir / filename
        if not path.exists():
            continue
        generation = generations[filename]
        try:
            bucket.blob(f"{prefix}/{filename}").upload_from_filename(
                path,
                if_generation_match=generation if generation is not None else 0,
            )
        except PreconditionFailed as error:
            raise RuntimeError(
                f"Remote state changed while the job was running: {filename}"
            ) from error


class GcpRuntime:
    name = "gcp"

    def __init__(self) -> None:
        self.delivery = None
        self.private_bucket = None

    def start(self) -> ScanSettings:
        private_bucket_name = os.environ["AG_PRIVATE_BUCKET"]
        delivery_bucket_name = os.environ["AG_DELIVERY_BUCKET"]
        model = os.environ.get("AG_LLM_MODEL", DEFAULT_MODEL)
        message_mode = resolve_message_mode()
        message_processor = resolve_message_processor()
        timezone = ZoneInfo(os.environ.get("AG_TIMEZONE", "Europe/Copenhagen"))
        refresh_id = os.environ.get("CLOUD_RUN_EXECUTION") or uuid.uuid4().hex
        client = storage.Client()
        self.private_bucket = client.bucket(private_bucket_name)
        self.delivery = GCSDeliveryStore(delivery_bucket_name)
        return ScanSettings(model, message_mode, message_processor, timezone, refresh_id)

    def workspace(self):
        return tempfile.TemporaryDirectory(prefix="ag-school-sync-")

    def checkout(self, state_dir: Path) -> dict[str, int | None]:
        generations = download_state(self.private_bucket, state_dir, prefix=RESTRICTED_PREFIX,
                                     filenames=RESTRICTED_STATE_OBJECTS)
        generations.update(download_state(self.private_bucket, state_dir, prefix="state",
                                          filenames=(SESSION,)))
        # The household's files, when the bucket has them; never uploaded.
        download_state(self.private_bucket, state_dir, prefix="state",
                       filenames=(*CONFIG_FILES, MEANING_FILE, TRIAGE_FILE))
        # The household's own house pictures, when it keeps any; never uploaded.
        folder = state_dir / ARTWORK_FOLDER
        for blob in self.private_bucket.list_blobs(prefix=f"state/{ARTWORK_FOLDER}/"):
            name = blob.name.rsplit("/", 1)[-1]
            if name.removesuffix(".webp") in PRESETS and name.endswith(".webp"):
                folder.mkdir(exist_ok=True)
                blob.download_to_filename(folder / name)
        return generations

    def commit(self, state_dir: Path, token: dict[str, int | None]) -> None:
        upload_state(self.private_bucket, state_dir, token, prefix=RESTRICTED_PREFIX,
                     filenames=RESTRICTED_STATE_OBJECTS)
        upload_state(self.private_bucket, state_dir, token, prefix="state", filenames=(SESSION,))

    def display_store(self):
        from skoleklar.display_store import display_store_from_environment
        return display_store_from_environment()

    def status_store(self):
        from skoleklar.board_status import status_store_from_environment
        return status_store_from_environment()

    def read_private(self, name: str) -> bytes | None:
        try:
            return self.private_bucket.blob(f"dashboard/{name}").download_as_bytes()
        except NotFound:
            return None

    def write_private(self, name: str, text: str) -> None:
        self.private_bucket.blob(f"dashboard/{name}").upload_from_string(text)

    def write_control(self, name: str, document: dict[str, Any]) -> None:
        if os.environ.get("AG_CONTROL_BUCKET"):
            GCSDeliveryStore(os.environ["AG_CONTROL_BUCKET"]).write_json(name, document)

    @contextlib.contextmanager
    def lock(self):
        # Covers direct gcloud executions too. No expiry takeover while a worker may
        # still be writing; a killed execution needs explicit operator reconciliation.
        from skoleklar.refresh_control import Conflict, GCSControlStore
        bucket = os.environ.get("AG_CONTROL_BUCKET")
        control = GCSControlStore(bucket) if bucket else None
        generation = None
        if control:
            try:
                generation = control.put("pipeline-lock.json",
                                         {"execution": os.getenv("CLOUD_RUN_EXECUTION", "manual")}, 0)
            except Conflict:
                raise RuntimeError("A refresh already owns the pipeline lock") from None
        try:
            yield
        finally:
            if control and generation is not None:
                control.delete("pipeline-lock.json", generation)
