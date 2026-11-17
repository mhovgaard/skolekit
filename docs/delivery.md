# Delivery contracts

For maintainers changing the phone, browser board, gateway or display client.
This describes implemented interfaces; per-installation acceptance is in the
builder guides, not inferred from a successful local test.

## Boundaries

The scan owns private facts and desired delivery state. The delivery service
reads published manifests/frames and accepts receipts, completions, refresh
requests and device status; it does not need the school database or login.
The primary iPhone owns Apple Calendar/Reminders writes. The ESP32 owns its
verified cache and physical draw. A status request alone proves neither write.

GCP uses four buckets: private state, phone delivery, mutable control and
current display frames. Only the private-state bucket holds source records
and session data. Display objects must not retain subject-bearing history.
The [GCP target state](../infra/gcp/target-state.md) specifies IAM and retention.

## Authentication

API Gateway requires a restricted `x-api-key`. The application additionally
checks a per-device bearer token and allowed scopes. Registry entries store
token hashes; device secrets and gateway keys stay in private storage.
Display-only tokens cannot become a school read or phone-write capability.

The cloud browser board sits behind IAP and verifies the asserted identity;
owner/viewer settings govern access and refresh. The local browser board is
loopback-only with local ownership. Serving it on a home LAN is not implemented.
Keep access logs and diagnostics free of tokens and school content.

## Phone

Exact JSON definitions are in [contracts](../src/skoleklar/contracts/):
`sync-request-v1`, `manifest-v2`, `receipt-v1` and `probe-request-v1`.

| Endpoint | Purpose |
| --- | --- |
| `POST /v1/probe` | Harmless authentication/contract check |
| `POST /v1/sync` | Submit completed stable keys and request a newer manifest |
| `POST /v1/receipts` | Report applied keys and failed native writes |

`/v1/sync` returns 204 when the caller already has the current manifest. A
missing, expired or unsupported manifest returns 503. Manifest v2 includes
calendar, reminder and review entries, each with a stable key, a presentation
`kind`, and a typed `fact` object. Calendar and reminder entries also carry a
revision used by the Shortcut. [Fact model](processing.md#the-fact-model)

The Shortcut reads the calendar and reminder sections and matches native
entries by the stable key embedded in their notes. It skips unchanged
revisions, updates changed entries, and refuses ambiguous matches. It leaves
existing native entries in place when a key disappears. One primary iPhone
reconciles entries. Other family members receive the shared iCloud calendars
and lists.

A receipt lists applied keys and failed writes separately. The next scan
records delivery for the applied keys and ingests completion observations.
The manifest includes a text brief, which can start with a scan health notice.
The Shortcut applies calendar and reminder entries. [iPhone setup](iphone-refresh-school.md)

## Refresh and display

| Endpoint | Purpose |
| --- | --- |
| `POST /v1/refresh` | Request a school-data scan when the token's scope permits |
| `GET /v1/refresh/{identifier}` | Read that request's progress/outcome |
| `GET /v1/display/status` | Current frame metadata, next transition and upcoming versions |
| `GET /v1/display/current` | Current display metadata for compatible clients |
| `GET /v1/display/frame?version=...` | Native Spectra 6 bytes for a current/upcoming version |
| `GET /v1/display/image?version=...` | PNG for that version |
| `POST /v1/display/device-status` | Latest board status/battery/wake report |
| `POST /v1/display/receipt` | Receipt contract; current firmware does not send physical-draw receipts |

A refresh request's body is exactly `{"request_id": "<UUID>"}`, chosen by the
client before sending. A new request returns 202. Repeating the same UUID after
an uncertain response returns the existing request (200, `duplicate`); another
request while a scan runs returns 409; a missing scope returns 403 and an
unknown token 401. If the dispatch itself is uncertain (503), the dispatch lock
stays held: reconcile it against the Cloud Run job's executions before clearing
it, never because time has passed, and never re-send the job request blindly.

Frame endpoints reject unknown/past versions with 409. Integrity metadata and
ETags describe the bytes; clients must check size, palette and checksum before
using a download. The [timeline](dashboard-timeline.md) selects a pre-rendered
frame at request time without a new model call.

The firmware reads display metadata into a 4,096-byte buffer, so keep the
status response, including its `upcoming` list, well under that. Only the latest
status report per device is kept, at `device-status/DEVICE_ID.json` in the
control bucket.

A board sync downloads the latest published frame. It does not invoke a
school-data refresh. The board's latest status is available in the authenticated
browser board at `/board-status`; PNG diagnostic routes are `/preview.png` and
`/preview.png?preview=1`. See [display format](esp32-eink-display.md) and
[device status](firebeetle-device-status.md).

## The Mac's local controller

`bootstrap-dashboard.py` registers a `local-controller` device with the
`refresh` and `display` scopes and keeps its token in
`~/.config/skoleklar/devices/local-controller.json`. The one-shot client uses it to ask
for a scan, check that request, or fetch the current board image. It takes the
gateway key from the Keychain:

```sh
.venv/bin/python -m skoleklar.device_client --config ~/.config/skoleklar/devices/local-controller.json \
  --base-url https://GATEWAY_HOST refresh
.venv/bin/python -m skoleklar.device_client --config ~/.config/skoleklar/devices/local-controller.json \
  --base-url https://GATEWAY_HOST status REQUEST_UUID
.venv/bin/python -m skoleklar.device_client --config ~/.config/skoleklar/devices/local-controller.json \
  --base-url https://GATEWAY_HOST image --output rendering/private/current/cloud.png
```

`refresh` prints its request ID before any network I/O; after an uncertain
answer, retry with `refresh --request-id REQUEST_UUID` rather than a new ID.
`image` checks the PNG's SHA-256 and palette; it does not prove a physical draw.
The client never schedules or polls. Revoke a device by removing its registry
entry and redeploying the delivery service, which loads the registry at start.

## Maintaining the boundary

Change schemas, application validation, gateway routes, device permissions
and callers together. The delivery API validates both sync responses and
receipt lookups against manifest v2. During a rollout from manifest v1, run the
updated scan to replace the current manifest. A sync still pointing to v1 returns
503, and receipts for v1 manifests return 409. The endpoint paths, sync request
and receipt body versions stay at v1. [GCP updates](../infra/gcp/README.md#updating-the-running-services)

Use synthetic contract/API tests and Shortcut-structure tests first. Real
acceptance must then check iOS import destinations, changed-item edits,
reminder timing, a repeated idempotent run, and a real board draw/status report.
Keep those device/project records private.
