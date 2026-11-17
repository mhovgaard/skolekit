# Dashboard timeline

For maintainers changing scheduling, expiry or frame selection. One scan draws
the frames needed for up to 36 hours; a layout transition does not require
another school read or model call.

## Three clocks

| Clock | Determines |
| --- | --- |
| Scan schedule | When school records and interpretations are refreshed |
| Layout timeline | Which pre-rendered view is valid at a given moment |
| Device wake | When the ESP32 checks and draws a publication |

Default scans are weekdays 05:45, and daily 12:30 and 19:30, using the school's
configured timezone. The morning scan catches substitutions and overnight
changes before school; midday catches the morning's messages before the
afternoon; the evening scan catches the afternoon, weekly plans and Sunday's
preparation for Monday. A change waits for the next successful scan. Scan times are configurable in `skoleklar.toml`.
The product's display timing policy currently uses Europe/Copenhagen; changing
the school timezone alone does not generalize all board timing.

## Build and reuse

The scan exports context and composes each school week it needs. A cache key
covers the relevant input and contract; unchanged inputs reuse the composition.
`dashboard_timeline.slice_starts` enumerates changes within expiry and the
36-hour horizon: midnight, weekday afternoon focus changes, weekend transitions,
and the [day-view](day-view.md) windows.

For every change time, the scan exports context again without another harvest,
then slices the schema 3 board state. Source freshness and inbox markers come
from that moment's context. Weekend rows are selected again from facts and current weekend
dates, with freed rows refilled and overflow recalculated. Weather, lessons,
day summaries and the parents' look-ahead are attached for that frame.
A failed week's model composition uses a plain validated fallback and is not
cached as a successful model result.

Frames that draw identically at adjacent moments are merged. The display store
publishes the whole timeline in `current.json`, rather than advancing a pointer
with a scheduled job for every layout transition.

## Serving a frame

A schema-2 timeline holds rendered frame bundles with `valid_from` and
`valid_until`. This is the published display format. The renderer's input is
the separate [schema 3 board state](../rendering/ag-school-dashboard-state/references/state.md).
The API selects the frame valid at request time and reports
`next_change_at` and upcoming versions through `/v1/display/status`.
Unknown or past frame versions return 409 when requested.

`valid_until` means a later frame takes over. `expires_at` means the underlying
school data is too old. Do not extend data expiry simply because a new render
was produced. Stale and partial coverage must remain visible.

Versions include the run identifier and frame checksum. Identical bytes share
a version within one run, but another scan creates a new version even when
pixels are unchanged. Current firmware redraw avoidance compares versions;
it does not deduplicate equal pixel hashes across runs.

## Scheduling and wake policy

Local schedules use launchd/systemd; cloud schedules are installed by
`infra/gcp/create-schedules.sh`. That script reads configured scan times and
leaves an existing paused job paused. Creating a schedule and enabling it are
separate acceptance decisions for the installation.

The board wakes after scan times, after a frame transition, for retries and
within its maximum sleep interval. Its wake times are a firmware policy; if
customizing scan times, also check that the device still wakes after them.
A board wake only pulls published frames; it does not start a school scan.
See [firmware](../firmware/README.md#power-and-wake-policy).

## Failure and cost behavior

Source/model/invariant failure before database commit stops the data stage.
After commit, phone and board outputs report independent outcomes. A failed
board publication leaves the old bundle, subject to its existing expiry.
Keep safe counts and fixed failure reasons in logs; private fact text belongs
outside logs and public acceptance records.

Measure run time, model usage and retained objects on the target installation.
Composition reuse avoids unnecessary model calls, but neither a fixed monthly
cost nor a free-tier entitlement is guaranteed by this repository.
