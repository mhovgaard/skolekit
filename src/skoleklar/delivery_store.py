from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterator, Protocol


class DeliveryObjectExistsError(RuntimeError):
    pass


class DeliveryObjectNotFoundError(RuntimeError):
    pass


class DeliveryStore(Protocol):
    def read_json(self, name: str) -> dict[str, Any]: ...

    def write_json(
        self, name: str, value: dict[str, Any], *, create_only: bool = False
    ) -> None: ...

    def list_json(self, prefix: str) -> Iterator[tuple[str, dict[str, Any]]]: ...


def _encoded_json(value: dict[str, Any]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _safe_object_name(name: str) -> str:
    path = Path(name)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError("Unsafe delivery object name")
    return path.as_posix()


class LocalDeliveryStore:
    def __init__(self, root: Path):
        self.root = root.expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def _path(self, name: str) -> Path:
        return self.root.joinpath(*Path(_safe_object_name(name)).parts)

    def read_json(self, name: str) -> dict[str, Any]:
        path = self._path(name)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise DeliveryObjectNotFoundError(name) from error

    def write_json(
        self, name: str, value: dict[str, Any], *, create_only: bool = False
    ) -> None:
        path = self._path(name)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        payload = _encoded_json(value)
        if create_only:
            try:
                descriptor = os.open(
                    path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
            except FileExistsError as error:
                raise DeliveryObjectExistsError(name) from error
            with os.fdopen(descriptor, "wb") as output:
                output.write(payload)
            return
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temporary.write_bytes(payload)
        temporary.chmod(0o600)
        temporary.replace(path)

    def list_json(self, prefix: str) -> Iterator[tuple[str, dict[str, Any]]]:
        prefix_path = self._path(prefix)
        if not prefix_path.exists():
            return
        for path in sorted(prefix_path.rglob("*.json")):
            yield path.relative_to(self.root).as_posix(), json.loads(
                path.read_text(encoding="utf-8")
            )


class GCSDeliveryStore:
    def __init__(self, bucket_name: str):
        from google.cloud import storage

        self.bucket = storage.Client().bucket(bucket_name)

    def read_json(self, name: str) -> dict[str, Any]:
        from google.api_core.exceptions import NotFound

        try:
            payload = self.bucket.blob(_safe_object_name(name)).download_as_bytes()
        except NotFound as error:
            raise DeliveryObjectNotFoundError(name) from error
        return json.loads(payload)

    def write_json(
        self, name: str, value: dict[str, Any], *, create_only: bool = False
    ) -> None:
        from google.api_core.exceptions import PreconditionFailed

        blob = self.bucket.blob(_safe_object_name(name))
        try:
            blob.upload_from_string(
                _encoded_json(value),
                content_type="application/json; charset=utf-8",
                if_generation_match=0 if create_only else None,
            )
        except PreconditionFailed as error:
            raise DeliveryObjectExistsError(name) from error

    def list_json(self, prefix: str) -> Iterator[tuple[str, dict[str, Any]]]:
        safe_prefix = _safe_object_name(prefix).rstrip("/") + "/"
        for blob in self.bucket.list_blobs(prefix=safe_prefix):
            if blob.name.endswith(".json"):
                yield blob.name, json.loads(blob.download_as_bytes())


def delivery_store_from_environment() -> DeliveryStore:
    bucket = os.environ.get("AG_DELIVERY_BUCKET")
    if bucket:
        return GCSDeliveryStore(bucket)
    # Locally, in the household's folder: never in a checkout of the code.
    from skolekit.state import state_dir

    root = Path(os.environ.get("AG_DELIVERY_ROOT") or state_dir() / "delivery")
    return LocalDeliveryStore(root)
