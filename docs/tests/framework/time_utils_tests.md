# Time Utils UTC Tests

`tests/framework/test_time_utils_utc.py` — that `parse_datetime` and `ensure_utc_aware`
(`python/framework/utils/time_utils.py`, §9) hand back UTC and nothing else. Runs under the
synthetic `framework/_root` suite.

## Why this file exists at all

`parse_datetime` promised a UTC-aware datetime and returned whatever zone `dateutil` chose:
`tzlocal()` for a string whose offset matched the machine's zone, a fixed offset for any other.
The instant was right every time, so no comparison and no duration ever went wrong — but
`.hour`, `.date()` and `.weekday()` answered in that zone. Every file this project writes carries
`+00:00` and the container runs in UTC, which is why it stayed invisible for ten months and
surfaced only as a repr (`tzinfo=tzlocal()`) inside an error message.

So the cases that matter run with the process's zone set to `Europe/Berlin` — a machine the
suite otherwise never runs on, and one a contributor may. The fixture sets `TZ`, calls
`time.tzset()`, and restores both afterwards.

## What Is Tested

| Test | Description |
|------|-------------|
| `test_every_offset_comes_back_as_utc` | `+00:00`, the machine's own `+01:00`, summer `+02:00` and `Z` — parametrized; `tzinfo` is exactly `timezone.utc` and the hour is the UTC hour |
| `test_a_naive_string_is_taken_as_utc` | no offset means UTC, never the machine's zone |
| `test_the_utc_day_is_the_day_near_midnight` | 00:30 on the 25th in Berlin is the 24th in UTC — `.date()` says the 24th |
| `test_an_aware_value_in_another_zone_is_converted_not_passed_through` | a `zoneinfo` New York value becomes 22:00 UTC; the instant is unchanged |
| `test_a_pandas_timestamp_stays_a_timestamp` | the coverage report hands in a bar's `Timestamp`; it comes back as one, in UTC |

## What Is Not Tested Here

Timezone CONVERSION between a local wall-clock time and UTC (`local_time_to_utc`, the swap
rollover) — that lives in [Market Calendar Tests](market_calendar_tests.md).
