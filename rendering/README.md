# Local rendering workflows

For agents preparing a private preview and maintainers editing board contracts.
The scripts in this directory wrap the application implementation; they are
not a separate renderer or a cloud deployment mechanism.

Install the project with `uv sync --extra board`. Use the configured writer
for any school-data refresh, then follow these workflows in order:

1. [Dashboard state](ag-school-dashboard-state/SKILL.md): export checked context,
   compose fact-reference slots, build state and its audit, then validate.
2. [HTML preview](ag-school-html-renderer/SKILL.md): render the same state in a
   browser and inspect desktop/narrow layouts.
3. [E-ink preview](ag-school-eink-renderer/SKILL.md): render the indexed 800×480
   PNG and inspect palette, fit and obligation/cancellation visibility.

The [state contract](ag-school-dashboard-state/references/state.md) and
[composition rules](ag-school-dashboard-state/references/composition.md) apply
to both outputs. Keep private input, audits and current output under ignored
`rendering/private/`. Nothing here marks a fact delivered or flashes a device.

Live subjects require an explicit opt-in and the separate
[disclosure workflow](ag-school-dashboard-state/references/live-subjects.md).
Inspect layouts with synthetic subjects; never create a history of real
subject-bearing screenshots. Runtime prompt bundles under `src/skoleklar/manifests/`
are versioned contracts and must not be edited as a documentation cleanup.
