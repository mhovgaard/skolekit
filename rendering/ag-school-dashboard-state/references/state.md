# Shared dashboard state v2

Both renderers consume the same UTF-8 JSON. The executable allowlist lives in
`src/skoleklar/dashboard_renderer.py`; unknown fields are rejected at every
level. Schema v1 remains readable only for existing previews. New compositions
must use schema v2.

```json
{
  "schema_version": 2,
  "week_label": "UGE 40",
  "updated_label": "DATA LØR 26. SEP. · 19.45",
  "freshness": {
    "source_updated_at": "2026-09-26T19:45:00+02:00",
    "composed_at": "2026-09-26T20:00:00+02:00",
    "expires_at": "2026-09-28T07:45:00+02:00",
    "status": "fresh"
  },
  "hero": {
    "state": "Næste skoledag",
    "day": "Mandag 28. sep.",
    "children": [
      {"name": "Alfa", "heading": "Idræt", "lines": ["Valgfrit: hinkesten"]},
      {"name": "Beta", "heading": "Dansk", "lines": ["Læs s. 52–53; lav s. 26"]}
    ]
  },
  "horizon_label": "Skoleugen 28. sep.–2. okt.",
  "past_summary": "Næste skoledag er mandag 28. sep.",
  "weekdays": [
    {
      "date": "2026-09-28",
      "day": "Man. 28. sep.",
      "children": [
        {"name": "Alfa", "heading": "Idræt", "lines": ["Valgfrit: hinkesten"]},
        {"name": "Beta", "heading": "Dansk", "lines": ["Læs s. 52–53; lav s. 26"]}
      ],
      "shared": null,
      "summary": "Idræt og dansk."
    }
  ],
  "weekend": [
    {"day": "Søn. 4. okt.", "text": "Begge: Svømmedag kan deltage"}
  ],
  "footer": "",
  "messages": {
    "checked": true,
    "bodies_retained": 0,
    "unread_count": 2,
    "items": []
  }
}
```

The example is abbreviated: production state contains exactly five `weekdays`
entries, Monday through Friday, in chronological order. Every weekday has its ISO
`date`, visible `day`, both children in hero order, optional `shared` text, and a
compact `summary`. The renderer uses the detailed content to estimate row height.
It unfolds all five when they fit; when they do not, it folds the dates farthest
from `freshness.composed_at` first and displays their summaries.

Both renderers draw schema v2 school-day states as the
[parents' board](../../../docs/parents-board.md): one row per child, the next
school day on the left and the four school days after it on the right. The table above
is the data; the fold order below applies to schema v1 previews only.

Hero requires exactly two distinct children in fixed order. Each child has a
topic heading and up to five action-preserving lines. It represents the current
school day before 15:00 and the next school day from 15:00. From Friday at 15:00
through Sunday before 12:00, it prepares the coming Monday behind the HTML
weekend panel. From Sunday at 12:00 it is shown as the next school day.

Each `weekend` row may carry `kinds`, written by code from the facts: one known
line kind per `;` part of its text after `who:` (an activity, then what to bring:
`["party", "bring"]`). Renderers draw the boxes and labels from it, and the
composition check compares an activity's row kind with the activity's category;
a row without `kinds` (an older state) is read from its words.

`weekend` has at most two rows of actual activities. During the weekend cover,
use only the current weekend. In school-day mode it is the place for still-relevant
activities on the current/upcoming or displayed-week closing weekend.
Calendar birth-date notices do not establish invitations or family attendance.
Both renderers reserve a fixed lower-right area: each activity is one row with its
day in the day column and the text across both child columns, prefixed in bold
with the child's name (`Dagny:`) or `Begge:`. An overflow count is an aside on the
heading row. Weekend rows never consume hero or weekday space, and `footer` stays
empty.

Messages contain `checked`, zero `bodies_retained`, the aggregate `unread_count`,
and at most two metadata-only rows. Each row may contain:

- `participant`: a shortened `First I.` label;
- `children`: one or both displayed child names;
- `activity`: a short display date or time;
- `unread`: boolean;
- optional `new_thread`, `new_message`, and `latest_unretrieved` booleans;
- optional `new` boolean: the row's latest activity is from the last 24 hours, or
  from Friday 15:00 on while the weekend panel shows.

`messages.new_count` counts those rows and the inbox line reads
`BESKEDER · 7 ULÆSTE (1 NY)`. Both are set by code, never by the model.

Never include message subjects, bodies, previews, quoted text, full surnames,
source URLs, raw IDs, or opaque handles. The one name a message fact keeps is a
birthday party's birthday child, by first name, in its title (`Claras fødselsdag`;
see [message privacy](../../../docs/message-harvesting.md#extraction-and-validation)). The
[live-subject overlay](live-subjects.md) is a separate render-time read with a
local sensitivity check and, when the household opts in, a one- or two-word topic
from the model; it never changes this serialized state.

## Parents' board fields

All optional, schema v2 only, and written by code, never by the model:

- `kinds` on a hero or weekday child: one entry per line, in line order, from
  `homework`, `bring`, `optional`, `deadline`, `time_change`, `change`,
  `substitute`, `trip`, `party`, `meeting`, `event`, `info`, `routine` and `more`.
  `time_change` is a change that moves a time (yellow within two days); `routine`
  a weekly event the board leaves out. `shared_kind` on a weekday names
  the kind of its `shared` line.
- `ahead`: at most six days after the composed week, from the day after the next
  school day to the fourth school day after it (Monday to Friday; a Saturday or
  Sunday in between only when it has facts), each `{"date", "children": [{"name",
  "lines", "kinds"}], "shared": [{"text", "kind"}]}` with at most five lines per
  child and two shared.
- `later_events`: at most six `{"date", "who", "kind", "text"}` after the fourth
  school day ahead and up to fourteen days after the next school day, where `who` is a
  child's name or `Begge`, `kind` is `trip`, `party`, `meeting`, `event` or
  `deadline`, and `text` is at most 60 characters.
- `device`: `{"battery_percent": 0–100}` from the board's own status report.

```json
"later_events": [
  {"date": "2026-11-19", "who": "Begge", "kind": "party", "text": "Skolefest 17.00–19.30"}
],
"device": {"battery_percent": 82}
```

## Review count

`review_count` is optional and set by code, never by the model: the number of
facts flagged for review in the shown week and weekend that the board leaves out,
after the [review checks](../../../docs/processing.md#review-checks) have settled what they can.
Both renderers show it before the update stamp as `2 punkter at tjekke`. It is
absent when there are none.

## Day summaries

`day_summaries` is optional and schema v2 only. It is never composed by the
model: the cloud sync attaches it to every frame from the stored
[weekly-plan day summaries](../../../docs/processing.md#day-summaries). The parents' board
shows the next school day's `headline` as each child's plan line on the left, and
each school day ahead's `headline` as the first line of that day on the right; the
day view below shows the summary for its date.

```json
"day_summaries": [
  {
    "date": "2026-09-28",
    "children": [
      {"name": "Alfa", "status": "ok", "headline": "Bogstavet E og hinkebaner",
       "text": "I dansk møder du edderkoppen Egon. Du tegner edderkoppen og skriver E. I idræt tegner I hinkebaner. Du må gerne tage en hinkesten med."},
      {"name": "Beta", "status": "not_published", "headline": null, "text": null}
    ]
  }
]
```

- One entry per shown school day, at most nine, one per date: each `date` is one
  of `weekdays[].date` or one of the four school days after the next school day
  (`parents_board.lookahead_dates`), which may fall in the following week.
- `children` has both children in hero order.
- `status` is `ok`, `no_content`, `not_published` or `unavailable`. Only `ok`
  carries a `headline` (at most 48 characters) and a single-line `text` (at
  most 480 characters, usually 150–300); otherwise both are null.

## Day view

`day_view` is optional and schema v2 only, never composed by the model and never
present with `weather`. The cloud sync attaches it from the stored calendar
lesson slots during the [children's windows](../../../docs/day-view.md). While it
is present both renderers draw the children's day view instead of the parents'
board, using the hero's lines, `day_weather` and `day_summaries` for its date.

```json
"day_view": {
  "date": "2026-09-28",
  "label": "I dag",
  "slots": ["09:00", "10:00", "10:45", "12:15", "13:00"],
  "children": [
    {"name": "Alfa", "lessons": [
      {"start": "09:00", "end": "10:45", "subject": "Dansk", "change": "vikar", "note": "Mette"},
      {"start": "12:15", "end": "13:45", "subject": "Idræt", "change": null, "note": ""}
    ]},
    {"name": "Beta", "lessons": [
      {"start": "09:00", "end": "09:45", "subject": "Musik", "change": "aflyst", "note": ""}
    ]}
  ]
}
```

- `date` is a school day and `label` is `I dag` or `I morgen`.
- `slots` is the day's bell schedule: one to ten ordered `HH:MM` starts, shared
  by both rows.
- `children` has both children in hero order, each with at most ten lessons in
  order. A lesson starts on a slot and ends after it.
- `subject` is display text of at most 32 characters; it may contain soft hyphens.
- `change` is null, `vikar` or `aflyst`. Only `vikar` has a `note`: the
  substitute's first name, at most 24 characters.

## Weekend weather

`weather` is optional and schema v2 only. It is never composed by the model:
`skoleklar.weather` fetches one Open-Meteo forecast for the configured place and
attaches the object after composition, from Friday 15:00 until Sunday 12:00 (on
Saturday from 18:00 and on Sunday it shows Sunday and Monday, so there are always
two days). While it is present both renderers switch
to the weekend layout: the left panel shows the daytime weather cover,
`weekend` activities and the inbox; the right panel shows the weather
instead of the school-week table. A failed forecast leaves the normal layout.

```json
"weather": {
  "place": "Byen",
  "verdict": "Udedag",
  "reason": "Tørt med sol, 9–19°. Godt vejr til at være ude.",
  "label": "Bedste tid ude",
  "note": "Cykeltur: søn. 13–17 · lyst til 18:37",
  "days": [
    {
      "date": "2026-10-03", "day": "Lør. 3. okt.", "sunrise": "07:16", "sunset": "18:40",
      "icon": "partly", "low": 12, "high": 19, "rain_mm": 0,
      "cells": [
        {"label": "Formiddag", "icon": "cloud", "temp": 15, "rain_mm": 0},
        {"label": "Efterm.", "icon": "partly", "temp": 19, "rain_mm": 0}
      ],
      "flags": [{"icon": "wind", "label": "9 m/s"}],
      "hours": "ssccccsscsssscc",
      "window": {"start": 14, "end": 18, "label": "Tør · 18–19°"}
    }
  ]
}
```

- `days`: two entries, Saturday and Sunday or Sunday and Monday (one if the forecast
  stops at Sunday). The verdict only judges the weekend days.
- Day-level `icon`, `low`, `high` and `rain_mm` describe 09:00–16:00 local time
  for the left-hand cover, regardless of refresh time. The icon reflects prevailing
  conditions; a brief shower does not dominate, but thunderstorms remain explicit.
  Older bundles without day-level `rain_mm` use the half-day totals as a fallback.
- `cells`: exactly two half-days (08–12 and 12–18) with an icon (`sun`, `partly`,
  `cloud`, `rain`, `snow`, `storm`, `fog`), the highest temperature and whole mm of rain.
- `flags`: at most two per day, in order of severity: `storm`, `wind` (9 m/s or
  more), `umbrella` (1 mm or more), `frost`, `uv` (5 or more).
- `hours`: one letter per hour from 06 to 20: `s` sun, `c` cloud, `r` rain or snow.
- `window`: the sunniest dry stretch in daylight (four hours, else three or two), or
  `null`. Evaluate the full daylight period, including hours before `composed_at`:
  refreshing later must not erase an earlier dry trip. The day-level verdict,
  flags and cycling recommendation also use the full waking day, not only remaining hours.
- `verdict`: `Udedag`, `Indedag`, `Blandet`, `Stranden`, `Skoven` or `Museum`. With
  `AG_WEATHER_MODE=private` a rideable window makes it `Cykeldag` and the timeline
  label `Bedste cykeltid`; in family mode the same window is only the `note`.

Configuration is environment-only: `AG_WEATHER_PLACE`, `AG_WEATHER_LATITUDE`,
`AG_WEATHER_LONGITUDE` (no default: without them the board shows no forecast;
coordinates are rounded to two decimals before the request), `AG_WEATHER_MODE` (`family` or `private`) and
`AG_WEATHER=off` to disable it.

Freshness timestamps require timezones. Status is `fresh`, `stale`, `partial` or
`unknown`. Expiry follows source update time. Stale rendering suppresses the
apparently current inbox count and warns visibly while keeping the last facts.
