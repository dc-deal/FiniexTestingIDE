# Server Clock Tests

`tests/framework/test_server_clock.py` — that `server_clock_to_utc_ms` and its single-value form
`server_clock_to_utc` (`python/framework/utils/time_utils.py`) turn a broker server's wall clock into
UTC through the zone's own daylight saving rules. Runs under the synthetic `framework/_root` suite.

## Why this file exists

The MT5 server runs on New York close time: New York plus seven hours, UTC+2 while New York keeps
standard time and UTC+3 in daylight time. The importer used to subtract a fixed three hours, which
stored every tick recorded in US winter one hour early — visible only on anchors whose UTC time is
known independently. The cases pin the rule on exactly those anchors: the US payroll release
(08:30 New York) and the weekly forex open (17:00 New York, midnight on the server).

## What Is Tested

| Test | Description |
|------|-------------|
| `test_the_anchor_lands_on_its_utc_time` | Payroll at 15:30 on the server is 13:30 UTC in winter and 12:30 in summer; Monday 00:00 on the server is Sunday 22:00 / 21:00 UTC — parametrized |
| `test_the_server_follows_the_us_switch_not_the_eu_switch` | Between the US and the EU switch dates the server is already three hours ahead |
| `test_a_stamp_inside_the_changed_hour_is_refused` | The hour New York repeats (2025-11-02) and the hour it skips (2026-03-08) raise instead of guessing |
| `test_the_minutes_around_the_changed_hour_still_convert` | The refusal is that hour only, not the whole Sunday morning |
| `test_order_and_length_are_kept` | Out-of-order input stays out of order — the importer converts a file in one call, unsorted |
| `test_milliseconds_survive` | Only whole hours move |
| `test_utc_at_zero_hours_is_left_as_it_is` | Kraken's clock: nothing to resolve |
| `test_an_empty_file_converts_to_nothing` | The conversion is never the step that fails on an empty file |
| `test_it_agrees_with_the_array_form` | The single-value form, used for the open time in a file name, answers the same — parametrized across both seasons |

## What Is Not Tested Here

Which rule each broker declares — that is
[Config Cascade Tests](config_cascade_tests.md#server-clock-declared-test_server_clock_declaredpy).
The conversion inside the import, with its header stamps and the refusal of a file — that is
[Import Pipeline Tests](../data/import_pipeline_tests.md).
