---
name: ag-school-eink-renderer
description: Render prepared Skoleklar dashboard state as an 800×480 six-colour Waveshare Spectra 6 PNG for the FireBeetle ESP32-E e-ink client. Use for device-ready school dashboards; compose fresh data with ag-school-dashboard-state first when needed.
---

# Skoleklar E-ink Renderer

Render the shared state with `scripts/render_dashboard.py`. For fresh data or a
missing state, first use [ag-school-dashboard-state](../ag-school-dashboard-state/SKILL.md).
The sibling [HTML skill](../ag-school-html-renderer/SKILL.md) previews the identical
state with the same project installation. Shared implementation lives in `../shared/dashboard.py`;
keep sibling directories together when moving this package. Resolve symlinks
before locating the repository.

## Workflow

1. Obtain or prepare a display model. Use only short, final display strings; the
   renderer is not a summarizer or ranking agent.
2. Validate message fields against [references/input-schema.md](references/input-schema.md).
   Reject subjects, bodies, previews, quoted history, full surnames, raw source
   identifiers, and message/thread handles.
3. Run:

   ```bash
   uv run --extra board python rendering/ag-school-eink-renderer/scripts/render_dashboard.py \
     rendering/private/dashboard-state.json --format png --output-dir rendering/private/eink
   ```

   Install the project with its `board` extra. Rendering runs on the computer
   or scan runtime; the FireBeetle consumes packed native frames.
4. Verify `dashboard.png` is exactly 800×480, mode `P`, and uses only the six
   Spectra 6 pixel codes `0x0`, `0x1`, `0x2`, `0x3`, `0x5`, and `0x6`. Code
   `0x4` is unused.
   Open it and inspect for clipped obligations before handing it off. The SHA-256
   file supports refresh-on-change. Omitting `--format` still emits both formats
   for compatibility with the original renderer invocation.

## Rendering contract

- The PNG uses the six nominal panel colours (black, white, yellow, red, blue,
  green) at their hardware palette indices and has no alpha channel. Keep text and
  geometry deterministic; do not shrink type to fit overflowing content.
- `/preview.png` is the exact device file. `/preview.png?preview=1` replaces its
  nominal colours with the configured panel-appearance estimates for monitor
  review; it must never replace the file delivered to the device.
- The HTML is standalone. Shared rendering may embed CSS, fonts and timing
  JavaScript, but must not fetch external assets or include tracking,
  credentials, raw source data or design commentary.
- The input owns temporal selection, deduplication, wording, and the hero day.
  The renderer applies the documented line limits and produces deterministic
  pixels. The ESP32 validates packed bytes and skips refresh by frame version;
  it does not render text or deduplicate equal pixel hashes across scans.
- Weekend input uses the coming Monday as hero, with weekend activities in one
  fixed lower-right block: one child column each and shared rows across both.
  The right panel unfolds all five weekdays when their
  measured content fits and folds the farthest days only when needed.
- When the state carries `weather`, the layout switches for the weekend: weekend
  activities, the next school day in brief and the inbox on the left; verdict,
  half-day icons, flags, sunrise/sunset and the best dry window on the right.
  Add `--weather auto` to fetch the forecast on the Mac during the weekend window,
  or `--weather force` to preview the upcoming weekend on any day. The device never
  fetches weather.
- When message coverage is off or stale, say so. Never turn missing coverage
  into a reassuring zero.

## Live subjects

Only when the household explicitly requests the sensitivity-checked live
subject overlay, follow
[the shared live-subject workflow](../ag-school-dashboard-state/references/live-subjects.md)
and add `--live-subjects`. Generate HTML and PNG together into
`rendering/private/current` to share one fetch and check per child. Sensitive or
uncertain subjects show `First I. (redacted)`. Accepted subjects are truncated by
pixel width. This is an ephemeral overlay; the saved state still rejects subjects.
Fetch on the authenticated Mac, not on the ESP32. Inspect layout using synthetic
subjects; do not create historical screenshots of live subject-bearing output.
