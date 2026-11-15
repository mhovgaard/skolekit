---
name: ag-school-html-renderer
description: Render a prepared Skoleklar dashboard state as standalone responsive HTML for Mac or browser viewing. Use for HTML school-dashboard previews; compose fresh data with ag-school-dashboard-state first when needed.
---

# Skoleklar HTML Renderer

Render the common state without changing its facts or ranking. For fresh data or
a missing state, first use the sibling
[dashboard-state skill](../ag-school-dashboard-state/SKILL.md). Read its
[state contract](../ag-school-dashboard-state/references/state.md) when editing
the input. Resolve paths through the actual repository, including when this skill
is invoked through a symlink.

From the repository root:

```bash
uv run --extra board python rendering/ag-school-html-renderer/scripts/render_html.py \
  rendering/private/dashboard-state.json --output-dir rendering/private/html
```

Use the project environment with its `board` extra so the shared renderer and
optional artwork paths have their dependencies. The script validates the shared allowlist,
escapes all display strings, and writes `dashboard.html` without external assets,
browser rendering dependencies. No deployment is involved.

Only for an explicitly requested live subject noticeboard, follow the
[live-subject workflow](../ag-school-dashboard-state/references/live-subjects.md).
Add `--live-subjects` and use `.venv/bin/python` (school HTTP dependencies required).
Write to `rendering/private/current`, overwriting the current output. The latest
subject for each child is checked locally in memory: sensitive or uncertain text
shows the shortened sender with `(redacted)`; approved text is truncated to fit.
Do not put subjects into saved state or create screenshots/history of live subjects.

Open the generated HTML in the in-app browser or a Codex file preview. Check the
desktop layout and narrow/mobile width, including the clock-selected current,
next-school-day or weekend left panel,
the two-column message block, the single two-column weekend block, five dynamically folding weekdays, long Danish
text, date labels, message counts and freshness warnings. Keep the page an information dashboard;
implementation notes and the composition audit belong outside the HTML. The HTML
uses the configured Spectra 6 appearance estimates so its surfaces resemble the
physical panel preview.

Use the same state file for the e-ink renderer. Do not silently create an HTML-only
summary or rewrite facts to fit. Fix shared wording in the state and rerender both
formats when content changes. A static page does not update itself: refresh the
context/state when requested and report the source timestamp with the result.
