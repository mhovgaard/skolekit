"""A single current bundle; subjects exist only inside current rendered artifacts."""
from __future__ import annotations

import base64
import hashlib
import io
import os
from datetime import UTC, datetime, timedelta

from skoleklar.dashboard_renderer import (
    PANEL_COLOUR_NAMES,
    pack_panel_frame,
    render_html,
    render_panel_preview,
    render_png,
    timestamp,
    validate_panel_image,
)
from skoleklar.delivery_store import GCSDeliveryStore, LocalDeliveryStore


class GCSCurrentDisplayStore(GCSDeliveryStore):
    def write_json(self, name, value, *, create_only=False):
        if name != "current.json":
            raise ValueError("Display storage permits only the current bundle")
        self.bucket.reload()
        policy = self.bucket.soft_delete_policy
        if (self.bucket.versioning_enabled or self.bucket.retention_period
                or self.bucket.default_event_based_hold
                or (policy and policy.retention_duration_seconds)):
            raise ValueError("Display storage must not retain artifact history")
        from google.api_core.exceptions import NotFound
        import json
        blob = self.bucket.blob(name)
        try:
            blob.reload()
            generation = int(blob.generation)
            if blob.temporary_hold or blob.event_based_hold:
                raise ValueError("Display object has a hold")
        except NotFound:
            generation = 0
        blob.cache_control = "private, no-store"
        blob.upload_from_string(json.dumps(value, ensure_ascii=False),
                                content_type="application/json", if_generation_match=generation)


class DisplayUnavailable(Exception):
    pass


def display_store_from_environment():
    bucket = os.getenv("AG_DISPLAY_BUCKET")
    if bucket:
        return GCSCurrentDisplayStore(bucket)
    root = os.getenv("AG_DISPLAY_ROOT")
    if root:
        from pathlib import Path
        return LocalDeliveryStore(Path(root))
    return None


def make_bundle(state, refresh_id, *, live_messages=None, now=None, expires_at=None):
    now = now or datetime.now(UTC)
    expires = expires_at or min(timestamp(state["freshness"]["expires_at"]), now + timedelta(hours=36))
    image = render_png(state, live_messages=live_messages)
    validate_panel_image(image)
    output = io.BytesIO()
    image.save(output, format="PNG")
    png = output.getvalue()
    frame = pack_panel_frame(image)
    return {"schema_version": 1,
            "metadata": {"version": refresh_id, "generated_at": now.isoformat(),
                         "source_updated_at": state["freshness"]["source_updated_at"],
                         "expires_at": expires.isoformat(),
                         "status": state["freshness"]["status"],
                         "sha256": hashlib.sha256(png).hexdigest(), "width": 800, "height": 480,
                         "palette_colours": 6, "pixel_format": "spectra6-indexed",
                         "panel_codes": PANEL_COLOUR_NAMES,
                         "frame_format": "spectra6-4bpp-high-nibble-first",
                         "frame_bytes": len(frame),
                         "frame_sha256": hashlib.sha256(frame).hexdigest()},
            "html": render_html(state, live_messages=live_messages),
            "png_base64": base64.b64encode(png).decode("ascii")}


def make_timeline(frames, refresh_id, *, live_messages=None, now=None):
    """One frame per moment the panel changes, each served under its own version.

    `frames` pairs each start time with the state shown from then on. A moment
    that changes nothing visible is merged into the frame before it. The version
    follows the panel bytes, so a device never redraws an identical frame.
    """
    now = now or datetime.now(UTC)
    entries = []
    for start, state in frames:
        bundle = make_bundle(state, refresh_id, live_messages=live_messages, now=now)
        metadata = bundle["metadata"]
        if (entries and entries[-1]["metadata"]["sha256"] == metadata["sha256"]
                and entries[-1]["html"] == bundle["html"]):
            continue
        metadata["version"] = f'{refresh_id}-{metadata["frame_sha256"][:12]}'
        metadata["valid_from"] = start.astimezone(UTC).isoformat()
        entries.append(bundle)
    if not entries:
        raise ValueError("A timeline needs at least one frame")
    for entry, following in zip(entries, entries[1:]):
        entry["metadata"]["valid_until"] = following["metadata"]["valid_from"]
    entries[-1]["metadata"]["valid_until"] = entries[-1]["metadata"]["expires_at"]
    return {"schema_version": 2, "refresh_id": refresh_id, "generated_at": now.isoformat(),
            "expires_at": entries[-1]["metadata"]["expires_at"], "frames": entries}


# How long an alert frame is served. A scan that publishes replaces it sooner.
ALERT_HOURS = 36


def make_alert_timeline(state, refresh_id, *, code, now=None):
    """The last board, marked with the warning triangle, served from now on.

    `state` is the last composed board (its freshness is long past, so it also
    says ÆLDRE DATA); `code` is the scan's fixed failure code.
    """
    now = now or datetime.now(UTC)
    bundle = make_bundle({**state, "alert": {"code": code}}, refresh_id, now=now,
                         expires_at=now + timedelta(hours=ALERT_HOURS))
    metadata = bundle["metadata"]
    metadata["version"] = f'{refresh_id}-{metadata["frame_sha256"][:12]}'
    metadata["valid_from"] = now.astimezone(UTC).isoformat()
    metadata["valid_until"] = metadata["expires_at"]
    return {"schema_version": 2, "refresh_id": refresh_id, "generated_at": now.isoformat(),
            "expires_at": metadata["expires_at"], "frames": [bundle]}


class VersionChanged(Exception):
    pass


def _timeline_frame(timeline, now, version):
    """The frame showing at `now`, or a requested current or upcoming one."""
    frames = [frame for frame in timeline["frames"]
              if timestamp(frame["metadata"]["valid_until"]) > now]
    if not frames:
        raise ValueError()
    selected = frames[0]
    if version is not None and version != selected["metadata"]["version"]:
        selected = next((frame for frame in frames if frame["metadata"]["version"] == version), None)
        if selected is None:
            raise VersionChanged()
    upcoming = [{key: frame["metadata"][key]
                 for key in ("version", "valid_from", "valid_until", "frame_sha256")}
                for frame in frames[1:]]
    return {**selected, "upcoming": upcoming}


def current_bundle(store, *, now=None, version=None):
    """The current frame, with its upcoming successors when the bundle is a timeline.

    A requested `version` selects that frame if it is current or upcoming;
    any other version raises VersionChanged.
    """
    now = now or datetime.now(UTC)
    try:
        if store is None:
            raise ValueError()
        bundle = store.read_json("current.json")
        if bundle["schema_version"] == 2:
            bundle = _timeline_frame(bundle, now, version)
        elif bundle["schema_version"] == 1:
            bundle = {**bundle, "upcoming": []}
        else:
            raise ValueError()
        if timestamp(bundle["metadata"]["expires_at"]) <= now:
            raise ValueError()
        if version is not None and version != bundle["metadata"]["version"]:
            raise VersionChanged()
        return bundle
    except VersionChanged:
        raise
    except Exception:
        raise DisplayUnavailable() from None


def png_bytes(bundle):
    image = base64.b64decode(bundle["png_base64"], validate=True)
    if hashlib.sha256(image).hexdigest() != bundle["metadata"]["sha256"]:
        raise DisplayUnavailable()
    return image


def simulated_png_bytes(bundle):
    """Return a monitor preview; the stored and device-served image stays exact."""
    from PIL import Image

    with Image.open(io.BytesIO(png_bytes(bundle))) as image:
        preview = render_panel_preview(image)
    output = io.BytesIO()
    preview.save(output, format="PNG", optimize=True)
    return output.getvalue()


def panel_frame_bytes(bundle):
    """Derive the ESP-ready 4bpp frame from the one current device artifact."""
    from PIL import Image

    with Image.open(io.BytesIO(png_bytes(bundle))) as image:
        frame = pack_panel_frame(image)
    metadata = bundle["metadata"]
    if (len(frame) != metadata.get("frame_bytes")
            or hashlib.sha256(frame).hexdigest() != metadata.get("frame_sha256")):
        raise DisplayUnavailable()
    return frame
