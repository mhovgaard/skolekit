# Documentation

The public documentation describes the current code and how to build and
maintain an installation. It does not describe the state of a particular
household's cloud project or devices. Commands run from the repository root;
uppercase identifiers such as `PROJECT_ID` and `NAME` are placeholders.

## For builders

| Read | Purpose |
| --- | --- |
| [Local setup](getting-started.md) | Install, configure a school and household, sign in, scan and use MCP |
| [Google Cloud setup](../infra/gcp/README.md) | Move the single writer into a dedicated project |
| [GCP agent runbook](../infra/gcp/SKILL.md) and [target state](../infra/gcp/target-state.md) | Provision or maintain cloud resources with explicit checks |
| [Build the board](board-build-guide.md) | Illustrated parts and assembly overview, wiring, firmware, frame fitting, power and acceptance |
| [iPhone setup](iphone-refresh-school.md) | Generate and validate the Calendar/Reminders Shortcut |
| [Device status](firebeetle-device-status.md) | Interpret status reports and troubleshoot a sleeping board |

## For maintainers and agents

| Read | Purpose |
| --- | --- |
| [Maintenance](maintenance.md) | Change boundaries, validation, release checks and known limitations |
| [Architecture](architecture.md) | Package ownership, the shared scan, configuration and delivery boundaries |
| [Source adapters](data-sources.md) | Supported reads, coverage, school wording and stable identities |
| [Data foundation](data-foundation.md) | Database layers, repositories, migrations, history and queries |
| [Processing](processing.md) | Deterministic rules, model stages, prompt versions, archival and review |
| [Message privacy](message-harvesting.md) | Live reads, optional extraction and durable-storage restrictions |
| [Delivery contracts](delivery.md) | Phone, browser and display APIs, authentication and acknowledgements |
| [Board generation](board-generation.md) | Context, model slots, deterministic layout, validation and fallback |
| [Parents' board](parents-board.md) and [children's day view](day-view.md) | Product layout and selection rules |
| [Timeline](dashboard-timeline.md) | Pre-rendered frame selection, expiry and scan/wake scheduling |
| [Event interpretation](dashboard-event-interpretation.md) | Cancellations, event identity and overflow limitations |
| [House artwork](weekend-artwork.md) | Generated defaults, private overrides and image packaging |
| [Display format](esp32-eink-display.md) and [frame cache](firebeetle-frame-cache.md) | Native pixels, download validation and flash safety |
| [Firmware](../firmware/README.md) | Build commands, device modes and firmware maintenance |
| [Rendering workflows](../rendering/README.md) | Local agent composition and shared HTML/e-ink previews |

Versioned model instructions under `src/skoleklar/manifests/` are runtime
resources. Their schemas and hashes are contracts, not general documentation
to edit during a prose cleanup. SQL migrations, API schemas, synthetic fixture
notes and third-party licence files likewise stay beside their owning code.

Public docs use synthetic examples. Keep deployment logs, real message samples,
serial transcripts, screenshots with household content and private acceptance
records outside the tracked documentation set.
