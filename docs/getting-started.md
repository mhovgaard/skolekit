# Local setup

For a builder installing their own school and household. Start with live reads,
then add the stored household product. Run commands from the repository root.

Local mode runs on macOS or Linux. The Keychain commands use the system
keyring; where none works, give the OpenRouter key as `OPENROUTER_API_KEY` and,
with message processing on, the handle key as `AG_SCHOOL_MESSAGE_HANDLE_KEY`.
Google Cloud setup, the iPhone Shortcut and the e-ink board need a Mac: their
helpers use the macOS Keychain, clipboard, `shortcuts` tool and serial driver.

## Install

Use Python 3.12 or newer and `uv`. For live HTTP reads with an existing session,
`uv sync --no-dev` is enough. For login and a local browser board:

```sh
uv sync --extra browser --extra board --extra server
uv run playwright install chromium
```

The [README](../README.md#installation-choices) lists optional integrations.
Development and the complete tests use `uv sync --all-extras`.

## Configure and sign in

Choose one scope:

| Scope | Commands | Private directory | Override |
| --- | --- | --- | --- |
| Toolkit only | `skolekit` | `~/.config/skolekit/` | `SKOLEKIT_HOME` |
| Household product | `skoleklar` | `~/.config/skoleklar/` | `AG_SCHOOL_HOME` |

The product's folder also holds the board's and local controller's files, under
`devices/`; nothing private is kept in the code's own folder. An installation from
before the rename still works from `~/.config/ag-skoleintra/` until it is moved
with `mv ~/.config/ag-skoleintra ~/.config/skoleklar`.

The two scopes have separate sessions and profiles. Product commands mount the
same live reads using the household's files.

For the household product:

```sh
uv run skoleklar profile write --host SCHOOL.m.skoleintra.dk --child NAME:STUDENT_ID:CALENDAR_CLASS
uv run skoleklar login
```

Repeat `--child` for each child. The three parts come from the school's own
pages. Sign in to SkoleIntra in an ordinary browser and open the child's page:

| Part | Where to find it | Looks like |
| --- | --- | --- |
| `SCHOOL` | The host in the address bar | `SCHOOL.m.skoleintra.dk` |
| `STUDENT_ID` | The number after the portal: `https://HOST/parent/STUDENT_ID/NAME/…` | `123456` |
| `NAME` | The name after the number, exactly as the address writes it | `Alfa` |
| `CALENDAR_CLASS` | In the browser's developer tools, the `className` parameter of the `calendareventsource/LessonsEvents` request the lessons calendar makes | `002` |

A numeric class such as `002` gives class level 2 and code `02`, and
`profile write` accepts only a numeric class. If the site's class is not a
number (say `4A`), write the profile with the level in its place (`004`), then
set that child's `calendar_class_name` in `profile.toml` to the site's value;
`class_level` and `class_code` stay as written. The `departmentIds` parameter
of the same request belongs in `department_ids`; the default is `["0"]`.

`profile write` creates `school.toml` and `profile.toml` and refuses an existing
file unless `--force` is given. Check class levels and class codes in those
files before the first scan and at each new school year. Use
[source adapters](data-sources.md) when another school's wording differs.

Check the login and the profile with one live read before the first scan:

```sh
uv run skoleklar check
uv run skoleklar schedule --child NAME
```

For toolkit-only use, substitute `skolekit profile write` and
`skolekit auth login`. Routine reads use HTTP, not a browser:

```sh
uv run skolekit schedule --child NAME
uv run skolekit calendar --child NAME
uv run skolekit weekly-plan --child NAME
uv run skolekit forms --child NAME --state open
uv run skolekit notices --child NAME
uv run skolekit school check --child NAME
```

The household product mounts the same commands, for example
`uv run skoleklar schedule --child NAME`.

Login is manual by default. Optional Keychain prefill uses
`skoleklar credentials set` followed by `skoleklar login --use-keychain`.
Unattended renewal additionally requires `--auto-submit --headless`; it
submits only a verified SkoleIntra login form. Session files and credentials
must stay private. A missing or expired session is a failure, not an empty week.

## Configure the single writer

Create `skoleklar.toml` beside the household profile:

```toml
[runtime]
where = "local"
scans = ["mon-fri 05:45", "12:30", "19:30"]
```

Optional tables configure `[weather]`, `[meaning]`, `[countdown]` and `[gcp]`.
No weather location is assumed. `[countdown]` adds the family's birthdays and
ferier to the parents' board's countdown ([the countdown](parents-board.md#countdown)). `school.toml` configures school wording and timezone;
`profile.toml` supplies children and classes. Optional `triage.toml` fills
invented prompt-example slots; it is not needed to store message text.
See [configuration ownership](architecture.md#configuration).

Configure an OpenRouter key through the private environment variable
`OPENROUTER_API_KEY` or the interactive Keychain command. Without
`[runtime] model`, the scan uses the packaged default model:

```sh
uv run skoleklar llm-credentials set
uv run skoleklar runtime show
uv run skoleklar scan
uv run skoleklar week
uv run skoleklar serve
```

The local board is `http://localhost:8787/`. A successful scan reports each
output separately. If publication fails, inspect `control/latest-run.json`
in the private directory; validated database state may already be committed.
Do not hide a partial or stale result by changing its timestamp.

Schedule local scans only after a manual scan succeeds:

```sh
uv run skoleklar scan-schedule show
uv run skoleklar scan-schedule install
```

This uses launchd on macOS or a systemd user timer on Linux. The machine must
be awake. Remove the schedule before moving the writer elsewhere.

## Messages are opt-in

The default mode is `off`; no inbox request is made by harvesting. Explicit
live message reads and sanitized extraction have different boundaries. Follow
[message privacy](message-harvesting.md) before enabling them. A live read's
stdout can still be retained by a terminal, redirect or calling agent.

## MCP servers

Install `uv sync --extra mcp` and run `uv run --extra mcp skolekit mcp`
for live reads using the toolkit's profile/session. The stored-record server
reads a copy of the household database, never the file a writer uses. With the
GCP runtime, pull the copy and serve it:

```sh
uv run --extra gcp skoleklar db pull --project PROJECT_ID
uv run --extra mcp skoleklar mcp
```

With the local runtime, back the database up into the private directory and
serve that copy; repeat the backup after a scan to see the new records:

```sh
sqlite3 ~/.config/skoleklar/school-restricted.db ".backup '$HOME/.config/skoleklar/mcp-copy.db'"
uv run --extra mcp skoleklar mcp --db ~/.config/skoleklar/mcp-copy.db
```

Register the same commands in the agent client's stdio MCP configuration.
For a GCP household, for example:

```json
{
  "mcpServers": {
    "skolekit": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/skolekit", "--extra", "mcp", "skolekit", "mcp"]
    },
    "skoleklar": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/skolekit", "--extra", "mcp", "--extra", "gcp",
               "skoleklar", "mcp", "--pull", "--project", "PROJECT_ID"]
    }
  }
}
```

`--pull` fetches a newer copy when the server starts; if the pull fails, the
server serves the copy it already has. A local household passes
`"--db", "/path/to/mcp-copy.db"` instead of `--pull` and its project.
Its tools are `week`, `board_context`, `trace`, `bookings` and `last_scan`;
all read the copy without writing or migrating it. The live server has one
message-text tool, `message_thread`, which reads one requested thread live.

## Move to the cloud

Use [GCP setup](../infra/gcp/README.md). Seed the verified local state and
transfer the private configuration through that workflow. Set the cloud's
and local household's `[runtime] where = "gcp"` at cutover and remove local
scheduling. Local writing commands then refuse the household database; use
`db pull` and read-only copies. Do not run two writers during migration.
