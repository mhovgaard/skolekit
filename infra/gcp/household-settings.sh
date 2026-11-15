#!/usr/bin/env bash
# Sourced by the deploy scripts: the household's settings from skoleklar.toml
# (`skoleklar runtime show --json`), so no script holds a household's values.
# Each variable can still be set in the environment; the environment wins.
#
#   HOUSEHOLD_JSON       the whole document
#   HOUSEHOLD_TIMEZONE   the school's timezone (school.toml)
#   HOUSEHOLD_MODEL      the model, or empty for the code's default
#   HOUSEHOLD_SCANS      one "CRON|TEXT" line per scan time
#   HOUSEHOLD_WEATHER_*  PLACE, LATITUDE, LONGITUDE, or empty: no forecast
#   HOUSEHOLD_OWNERS / HOUSEHOLD_VIEWERS   comma-separated Google addresses

if ! HOUSEHOLD_JSON="$(uv run --quiet skoleklar runtime show --json)"; then
  echo "The household's skoleklar.toml could not be read; deployment stopped." >&2
  exit 1
fi
_household() { jq -r "$1" <<<"${HOUSEHOLD_JSON}"; }
HOUSEHOLD_TIMEZONE="${AG_TIMEZONE:-$(_household '.timezone // "Europe/Copenhagen"')}"
HOUSEHOLD_MODEL="${AG_LLM_MODEL:-$(_household '.model // empty')}"
HOUSEHOLD_SCANS="$(_household '.scans[] | "\(.cron)|\(.text)"')"
HOUSEHOLD_WEATHER_PLACE="${AG_WEATHER_PLACE:-$(_household '.weather.place // empty')}"
HOUSEHOLD_WEATHER_LATITUDE="${AG_WEATHER_LATITUDE:-$(_household '.weather.latitude // empty')}"
HOUSEHOLD_WEATHER_LONGITUDE="${AG_WEATHER_LONGITUDE:-$(_household '.weather.longitude // empty')}"
HOUSEHOLD_OWNERS="$(_household '.gcp.owners | join(",")')"
HOUSEHOLD_VIEWERS="$(_household '.gcp.viewers | join(",")')"
