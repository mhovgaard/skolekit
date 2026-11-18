# Skolekit

**A calmer school week for the whole family.**

**Skolekit** is the umbrella project and its toolkit for live school access.
**Skoleklar** is the family app built on it: it aggregates school information
into a checked household plan, dashboards and reminders.

Homework in the weekly plan. A packing list in a teacher's message. A class
party in a parent conversation. A changed lesson in the timetable. Keeping up
means more than reading everything: you have to remember which child it concerns,
when it matters, and what someone needs to do.

Skoleklar brings those details into a family overview: what is happening,
what to prepare, and what needs attention next. Start with Skolekit's local CLI
and MCP tools, add Skoleklar for a checked school week, or put its shared
dashboard on the wall where the family can see it.

![The parents' dashboard in a wooden e-ink frame beside a paper timetable and wall calendar](imgs/readme/parents-in-situ.png)

*An illustrated home setup based on the author's installation, with invented
family details. [See the parents' screen at full size](imgs/readme/parents-board.png).*

The project is self-hosted and currently connects to SkoleIntra. Use Skolekit
on its own for live school lookups, or add Skoleklar for the family planner,
browser dashboard and optional DIY e-ink display.

## Less to keep in your head

School communication arrives a piece at a time. Parents need to turn it into a
plan for the household. Skoleklar helps with that daily work:

- **Know what to do tonight.** See each child's homework, things to bring,
  deadlines and next school day together, instead of piecing them together
  across plans, notices and calendars.
- **Catch the details inside conversations.** With message processing enabled,
  school and parent conversations in SkoleIntra can contribute event dates and
  bring-items to the overview, including class parties and trips.
- **Prepare before the morning rush.** Check clothing for the weather, the
  packing list, lessons and finishing times before anyone heads out.
- **See beyond tomorrow.** Look ahead to trips, meetings, class events and
  deadlines, with a row for each child and a shared row for both.
- **Share the remembering.** Give parents and children a common place to check
  the day, on the browser dashboard or the shared e-ink board.
- **Let the weekend feel like the weekend.** Switch from homework to family
  activities, weather and a suggested dry window for a trip outside.
- **Check where a detail came from.** The stored school week keeps the source
  of its facts; uncertain items and older data are flagged for attention.

Messages are opt-in. A clean installation does not read the inbox during scans.
Enabled processing extracts a limited set of event facts; it does not keep a
copy of the conversations. Message-derived times require a live source check,
and attachment text is not extracted. See [message privacy](docs/message-harvesting.md)
for the choices and boundaries.

## A dashboard for parents, a day view for children

Skoleklar's **parents' board** puts the next school day on the left and the days ahead
on the right. Homework has checkboxes, events have category labels, and urgent
deadlines or changed times get a yellow highlight. Each child has their own row,
so you can see who needs what without mixing their weeks together.

The **children's view** answers the questions that matter before school:
*What are my lessons? When do I finish? What should I bring?* A short retelling
of the day's plan adds context, and the **Husk** list brings clothing, packing
and homework reminders together.

![The children's dashboard in the same home setting, with lessons, finishing times, weather and a packing list](imgs/readme/kids-in-situ.png)

*The children's day view, with invented school data and the display's six-colour
palette. [See the children's screen at full size](imgs/readme/kids-board.png).*

The board switches views with the family day: children see today's lessons
before school and the next school day in the afternoon; parents get the wider
overview in the evening. The browser and e-ink versions use the same prepared
dashboard state, including the weekend mode below.

A framed e-ink board fits beside the paper timetable and calendar. Everyone
can glance at it without opening an app or borrowing a parent's phone. It keeps
its picture while the device sleeps, then wakes to download and draw the next
prepared frame.

Read more about the [parents' board](docs/parents-board.md),
[children's day view](docs/day-view.md), [weekend cover](docs/weekend-artwork.md)
and [automatic view changes](docs/dashboard-timeline.md).

## A weekend view with room for family life

The board can take a break from homework too. With weather configured, Skoleklar
switches to a **weekend mode focused on family activities and time outdoors**
from Friday afternoon until Sunday noon. The lesson and homework layout gives
way to weekend plans, seasonal house artwork and a two-day weather overview.
On Sunday afternoon, school preparation returns for the coming week.

The forecast suggests a **trip window**: a dry stretch within daylight hours,
shown on each day's sunrise-to-sunset arc. It looks for four hours first, then
three or two, favouring sunshine, warmth and calmer wind. If there is no dry
window, the board says so. A short weather verdict helps the family decide
whether it is a day for being outside, a forest walk or an indoor outing.

![The weekend dashboard in the same wooden e-ink frame, with family activities, autumn house artwork, weather and suggested trip windows](imgs/readme/weekend-in-situ.png)

*An illustrative home scene using synthetic plans and forecasts. Saturday's
suggested trip window is **10–14**, alongside a forest walk and a Sunday board-game
afternoon. [See the weekend screen at full size](imgs/readme/weekend-board.png).*

Weather is optional; configure your place in `skoleklar.toml` to enable this
view. See the [weather setup](infra/gcp/README.md#first-setup),
[weekend artwork](docs/weekend-artwork.md) and
[view timing](docs/dashboard-timeline.md).

## Start local. Grow into a family dashboard.

Start with Skolekit for live school access. Skoleklar builds on it to aggregate
and interpret information across the household, then deliver it to the browser
or wall display, with an optional phone manifest. Choose the level that fits
your family:

| Level | What you get | Why it helps |
| --- | --- | --- |
| **1 · Skolekit: local toolkit** | `skolekit` CLI and MCP server for live lessons, calendars, plans, forms, notices and requested message reads | Look up school information from your terminal or through an assistant, without a school-record database or model |
| **2 · Skoleklar: family planner** | `skoleklar` scans, checked household records, school-week CLI and stored-record MCP server | Bring each child's week together, trace facts to their sources and ask questions across the household |
| **3 · Skoleklar: dashboard and phone manifest** | A browser board with optional GCP hosting and an optional iPhone Shortcut for shared Calendar and Reminders | Share the family overview in the browser and sync checked events and reminders to the phone |
| **4 · Skoleklar: e-ink in the home** | An 800×480 six-colour Waveshare Spectra 6 display with a FireBeetle ESP32-E | Put the school day in a shared physical place, with automatic views for parents, children and weekends |

The first two levels run locally on macOS or Linux. The browser board can also
run locally; remote access and the e-ink device use the documented GCP setup.
Cloud provisioning, iPhone and hardware helpers currently need a Mac. The
e-ink option is a build-it-yourself device; no enclosure design is supplied.

Both Python packages currently ship in the `skoleklar` distribution. The
`skolekit` package and command handle live school reads; `skoleklar` adds the
household database, aggregation, interpretation and delivery. Each has its own
MCP server, so an assistant can use live lookups or the aggregated family view.
For the technical design, see [architecture](docs/architecture.md).

**Phone setup:** A scan publishes a v2 phone manifest. With **Refresh School**
installed on the primary iPhone, the Shortcut applies calendar and reminder
entries to the shared iCloud destinations and sends reminder completions back.
See the [iPhone guide](docs/iphone-refresh-school.md) for setup and device checks.

## Technology stack

| Layer | Technologies |
| --- | --- |
| Core & tooling | [![Python 3.12+](https://img.shields.io/badge/Python%203.12%2B-3776AB?style=flat&logo=python&logoColor=white)](https://www.python.org/) [![SQLite](https://img.shields.io/badge/SQLite-003B57?style=flat&logo=sqlite&logoColor=white)](https://sqlite.org/) [![uv](https://img.shields.io/badge/uv-5A2C68?style=flat&logo=uv&logoColor=white)](https://docs.astral.sh/uv/) [![Hatchling](https://img.shields.io/badge/Hatchling-4051B5?style=flat&logo=data%3Aimage%2Fsvg%2Bxml%3Bbase64%2CPHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAyNCI%2BPHBhdGggZD0iTTMgNyAxMiAybDkgNXYxMGwtOSA1LTktNVY3em0wIDAgOSA1IDktNU0xMiAxMnYxME03IDQuOGwxMCA1LjUiIGZpbGw9Im5vbmUiIHN0cm9rZT0id2hpdGUiIHN0cm9rZS13aWR0aD0iMS44IiBzdHJva2UtbGluZWNhcD0icm91bmQiIHN0cm9rZS1saW5lam9pbj0icm91bmQiLz48L3N2Zz4%3D&logoColor=white)](https://hatch.pypa.io/) [![pytest](https://img.shields.io/badge/pytest-0A9EDC?style=flat&logo=pytest&logoColor=white)](https://docs.pytest.org/) |
| School access | [![HTTPX](https://img.shields.io/badge/HTTPX-4051B5?style=flat&logo=data%3Aimage%2Fsvg%2Bxml%3Bbase64%2CPHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAyNCI%2BPHBhdGggZD0iTTMgOGgxOG0tNC00IDQgNC00IDRNMjEgMTZIM200LTQtNCA0IDQgNCIgZmlsbD0ibm9uZSIgc3Ryb2tlPSJ3aGl0ZSIgc3Ryb2tlLXdpZHRoPSIxLjgiIHN0cm9rZS1saW5lY2FwPSJyb3VuZCIgc3Ryb2tlLWxpbmVqb2luPSJyb3VuZCIvPjwvc3ZnPg%3D%3D&logoColor=white)](https://www.python-httpx.org/) [![Beautiful Soup](https://img.shields.io/badge/Beautiful%20Soup-476A30?style=flat&logo=data%3Aimage%2Fsvg%2Bxml%3Bbase64%2CPHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAyNCI%2BPHBhdGggZD0iTTMgNyAxMiAybDkgNXYxMGwtOSA1LTktNVY3em0wIDAgOSA1IDktNU0xMiAxMnYxME03IDQuOGwxMCA1LjUiIGZpbGw9Im5vbmUiIHN0cm9rZT0id2hpdGUiIHN0cm9rZS13aWR0aD0iMS44IiBzdHJva2UtbGluZWNhcD0icm91bmQiIHN0cm9rZS1saW5lam9pbj0icm91bmQiLz48L3N2Zz4%3D&logoColor=white)](https://www.crummy.com/software/BeautifulSoup/bs4/doc/) [![JSON Schema](https://img.shields.io/badge/JSON%20Schema-555555?style=flat&logo=json&logoColor=white)](https://python-jsonschema.readthedocs.io/) [![keyring](https://img.shields.io/badge/keyring-556677?style=flat&logo=data%3Aimage%2Fsvg%2Bxml%3Bbase64%2CPHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAyNCI%2BPHBhdGggZD0iTTE0IDEwYTUgNSAwIDEgMC00IDRsMyAzaDN2M2g0di00bC02LTZaIiBmaWxsPSJub25lIiBzdHJva2U9IndoaXRlIiBzdHJva2Utd2lkdGg9IjEuOCIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5kIi8%2BPC9zdmc%2B&logoColor=white)](https://keyring.readthedocs.io/) [![Playwright](https://img.shields.io/badge/Playwright-2E7D32?style=flat&logo=data%3Aimage%2Fsvg%2Bxml%3Bbase64%2CPHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAyNCI%2BPHBhdGggZD0iTTMgM2gxOHYxOEgzek0zIDhoMThNNiA1LjVoLjAxTTkgNS41aC4wMW0tMiA5IDMgMyA3LTciIGZpbGw9Im5vbmUiIHN0cm9rZT0id2hpdGUiIHN0cm9rZS13aWR0aD0iMS44IiBzdHJva2UtbGluZWNhcD0icm91bmQiIHN0cm9rZS1saW5lam9pbj0icm91bmQiLz48L3N2Zz4%3D&logoColor=white)](https://playwright.dev/python/) [![Chromium](https://img.shields.io/badge/Chromium-4285F4?style=flat&logo=data%3Aimage%2Fsvg%2Bxml%3Bbase64%2CPHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAyNCI%2BPHBhdGggZD0iTTMgM2gxOHYxOEgzek0zIDhoMThNNiA1LjVoLjAxTTkgNS41aC4wMW0tMiA5IDMgMyA3LTciIGZpbGw9Im5vbmUiIHN0cm9rZT0id2hpdGUiIHN0cm9rZS13aWR0aD0iMS44IiBzdHJva2UtbGluZWNhcD0icm91bmQiIHN0cm9rZS1saW5lam9pbj0icm91bmQiLz48L3N2Zz4%3D&logoColor=white)](https://www.chromium.org/) |
| APIs & interpretation | [![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=flat&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/) [![Uvicorn](https://img.shields.io/badge/Uvicorn-4051B5?style=flat&logo=data%3Aimage%2Fsvg%2Bxml%3Bbase64%2CPHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAyNCI%2BPHBhdGggZD0ibTQgNSA2IDctNiA3bTkgMGg3IiBmaWxsPSJub25lIiBzdHJva2U9IndoaXRlIiBzdHJva2Utd2lkdGg9IjEuOCIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5kIi8%2BPC9zdmc%2B&logoColor=white)](https://www.uvicorn.org/) [![MCP](https://img.shields.io/badge/MCP-555555?style=flat&logo=modelcontextprotocol&logoColor=white)](https://modelcontextprotocol.io/) [![OpenRouter](https://img.shields.io/badge/OpenRouter-6467F2?style=flat&logo=openrouter&logoColor=white)](https://openrouter.ai/) [![Open-Meteo](https://img.shields.io/badge/Open--Meteo-2876AB?style=flat&logo=data%3Aimage%2Fsvg%2Bxml%3Bbase64%2CPHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAyNCI%2BPHBhdGggZD0iTTcgNFYxTTMgNiAxIDRtMTAgMiAyLTJNNyA4YTQgNCAwIDEgMC00IDRtNCA4aDExYTQgNCAwIDAgMCAwLTggNiA2IDAgMCAwLTExIDMgMi41IDIuNSAwIDAgMCAwIDVaIiBmaWxsPSJub25lIiBzdHJva2U9IndoaXRlIiBzdHJva2Utd2lkdGg9IjEuOCIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5kIi8%2BPC9zdmc%2B&logoColor=white)](https://open-meteo.com/) |
| Dashboard & phone | [![HTML](https://img.shields.io/badge/HTML-E34F26?style=flat&logo=html5&logoColor=white)](https://developer.mozilla.org/en-US/docs/Web/HTML) [![CSS](https://img.shields.io/badge/CSS-663399?style=flat&logo=css&logoColor=white)](https://developer.mozilla.org/en-US/docs/Web/CSS) [![JavaScript](https://img.shields.io/badge/JavaScript-75630B?style=flat&logo=javascript&logoColor=white)](https://developer.mozilla.org/en-US/docs/Web/JavaScript) [![Pillow](https://img.shields.io/badge/Pillow-306998?style=flat&logo=data%3Aimage%2Fsvg%2Bxml%3Bbase64%2CPHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAyNCI%2BPHBhdGggZD0iTTMgM2gxOHYxOEgzek0zIDE3bDYtNiA0IDQgMy0zIDUgNU0xNSA3aC4wMSIgZmlsbD0ibm9uZSIgc3Ryb2tlPSJ3aGl0ZSIgc3Ryb2tlLXdpZHRoPSIxLjgiIHN0cm9rZS1saW5lY2FwPSJyb3VuZCIgc3Ryb2tlLWxpbmVqb2luPSJyb3VuZCIvPjwvc3ZnPg%3D%3D&logoColor=white)](https://pillow.readthedocs.io/) [![Apple Shortcuts](https://img.shields.io/badge/Apple%20Shortcuts-7456BA?style=flat&logo=data%3Aimage%2Fsvg%2Bxml%3Bbase64%2CPHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAyNCI%2BPHBhdGggZD0ibTEyIDIgMTAgNi0xMCA2TDIgOCAxMiAyWm0tOSAxMSA5IDUgOS01TTMgMThsOSA1IDktNSIgZmlsbD0ibm9uZSIgc3Ryb2tlPSJ3aGl0ZSIgc3Ryb2tlLXdpZHRoPSIxLjgiIHN0cm9rZS1saW5lY2FwPSJyb3VuZCIgc3Ryb2tlLWxpbmVqb2luPSJyb3VuZCIvPjwvc3ZnPg%3D%3D&logoColor=white)](https://support.apple.com/guide/shortcuts/welcome/ios) [![iCloud](https://img.shields.io/badge/iCloud-2876AB?style=flat&logo=icloud&logoColor=white)](docs/iphone-refresh-school.md) |
| Cloud · optional | [![Docker](https://img.shields.io/badge/Docker-2496ED?style=flat&logo=docker&logoColor=white)](https://www.docker.com/) [![Google Cloud](https://img.shields.io/badge/Google%20Cloud-4285F4?style=flat&logo=googlecloud&logoColor=white)](infra/gcp/README.md) [![Shell scripts](https://img.shields.io/badge/Shell%20scripts-4EAA25?style=flat&logo=gnubash&logoColor=white)](infra/gcp/README.md) |
| E-ink · optional | [![C++](https://img.shields.io/badge/C%2B%2B-00599C?style=flat&logo=cplusplus&logoColor=white)](firmware/README.md) [![Arduino](https://img.shields.io/badge/Arduino-00878F?style=flat&logo=arduino&logoColor=white)](https://docs.arduino.cc/arduino-cli/) [![ESP32](https://img.shields.io/badge/ESP32-E7352C?style=flat&logo=espressif&logoColor=white)](firmware/README.md) [![Waveshare Spectra 6](https://img.shields.io/badge/Waveshare%20Spectra%206-2876AB?style=flat&logo=data%3Aimage%2Fsvg%2Bxml%3Bbase64%2CPHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAyNCI%2BPHBhdGggZD0iTTIgNGgyMHYxNUgyek03IDIzaDEwTTEyIDE5djRNNSA3aDV2OUg1ek0xMyA3aDZNMTMgMTFoNk0xMyAxNWg2IiBmaWxsPSJub25lIiBzdHJva2U9IndoaXRlIiBzdHJva2Utd2lkdGg9IjEuOCIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5kIi8%2BPC9zdmc%2B&logoColor=white)](docs/board-build-guide.md) |

## Build your own

The optional wall board brings together a **Waveshare 7.3-inch Spectra 6
display**, a **FireBeetle 2 ESP32-E with headers**, a **3.7 V LiPo battery** and
a frame of your choice. The display kit's HAT and ribbon connect the panel to
the controller; jumper leads complete the electrical build without soldering.

![Annotated assembly overview showing the e-paper panel, FireBeetle controller, LiPo battery, driver HAT, ribbon and wooden frame](imgs/build/01-parts-overview.png)

*Your own dashboard, inside a frame. A cleaned, AI-assisted illustration of the
author's parts; the [illustrated build guide](docs/board-build-guide.md) covers
the shopping list, assembly overview, wiring, tests and fitting the frame.*

Start with [local setup](docs/getting-started.md) for the application. It covers installation,
private configuration, login, model credentials and the first checked scan.
No school or household is bundled with the code. For the physical board, use
the [assembly overview](docs/board-build-guide.md#assembly-at-a-glance) and the
detailed device steps once the cloud service is ready.

```sh
uv sync --extra browser --extra board --extra server
uv run playwright install chromium
uv run skoleklar profile write --host SCHOOL.m.skoleintra.dk --child NAME:STUDENT_ID:CALENDAR_CLASS
uv run skoleklar login
```

Then set `[runtime] where = "local"` in the private `skoleklar.toml` file and
configure the model key as described in the setup guide:

```sh
uv run skoleklar scan
uv run skoleklar week
uv run skoleklar serve
```

The board is served on this machine at `http://localhost:8787/`. Keep exactly
one runtime as the database writer. Moving to GCP is an explicit setup step.

| Next step | Guide |
| --- | --- |
| Read the school's live pages or use the two MCP servers | [Local setup](docs/getting-started.md) |
| Run scheduled scans and access the board away from home | [Google Cloud setup](infra/gcp/README.md) and its [agent runbook](infra/gcp/SKILL.md) |
| Build the Waveshare Spectra 6 / FireBeetle ESP32-E board | [Parts, wiring, flashing and calibration](docs/board-build-guide.md) |
| Explore the currently unused phone manifest and iPhone integration | [Refresh School on iPhone](docs/iphone-refresh-school.md) |
| Understand or maintain the application | [Architecture](docs/architecture.md) and [maintenance guide](docs/maintenance.md) |

The [documentation index](docs/README.md) lists the maintained builder guides
and technical references.

## Installation choices

Python 3.12 or newer is required. The base installation is
`uv sync --no-dev`; add only the integrations you use:

| Extra | Adds |
| --- | --- |
| `browser` | Playwright for login and session renewal |
| `board` | Pillow for board rendering and artwork packaging |
| `server` | FastAPI and Uvicorn for the browser board and delivery API |
| `gcp` | Cloud Storage for cloud scans and database pulls |
| `mcp` | Local MCP servers |

For development, use `uv sync --all-extras` and follow the
[validation workflow](docs/maintenance.md#validation). Both Dockerfiles name
their runtime extras explicitly.

## Scope and limits

School HTTP access is enforced as read-only. There is no form submission,
message sending, read-status update, booking submission or school calendar edit.
School wording is configured over a Danish locale; the product's display text
and timing policy currently target Danish schools and Europe/Copenhagen.

The local board is loopback-only. Remote access uses the GCP deployment; LAN
serving with HTTPS and device authentication is not implemented. E-ink firmware
requires the documented FireBeetle ESP32-E, and updates use USB rather than OTA.
The iPhone workflow needs device acceptance testing on each installation.
[Known limitations](docs/maintenance.md#known-limitations) distinguish implemented
code from checks a builder must perform on their own cloud, phone and hardware.

Model interpretation can be wrong, and a failed scan can leave older data on
the board. Check the school's own site for anything that matters. Runtime and
model usage can incur charges; measure your own deployment rather than relying
on another household's usage history. Battery life also depends on the build
and must be measured on the assembled device.

## Licence

Free for noncommercial use under the
[PolyForm Noncommercial License 1.0.0](LICENSE.md). Commercial use needs a
separate licence from the author. The project is unofficial and is not
affiliated with SkoleIntra or any school. It is supplied without warranty;
see [NOTICE.md](NOTICE.md) for the required notice, responsibilities and
third-party licences.
