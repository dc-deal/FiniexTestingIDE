# Account Drawdown Period Tests

`tests/framework/test_account_drawdown_periods.py` — the five deepest drawdown periods of an
account's equity curve (#529), kept by `PortfolioManager` beside the deepest decline #497 measures.
Runs under the synthetic `framework/_root` suite.

## Why this file exists

One figure cannot tell an account that fell 12 % once from one that fell 11 % five times. The
portfolio keeps the five deepest drawdown periods of its curve — each from a peak until it was recovered —
and the tests hold the definition, the ranking, the hot-path discipline and the carry-over.

## What it checks

| Class / test | What it validates |
|---|---|
| `TestAPeriodRunsFromAPeakUntilItIsRecovered` | a period opens below the peak and closes back at it, with its three instants in order; a long fall and rebound is one period; a new peak closes one and measures the next from there; an account that only rose has none |
| `TestTheFiveDeepest` | five kept deepest first and a shallower sixth left out; a deeper one later displaces the shallowest; ranked by share of the peak, not by amount; the deepest is the period whose depth `account_max_dd_pct` measures; the open one ranks among them; a period with no share — its peak not above zero — ranks behind every one with a share, by its amount |
| `TestTheClock` | a sample that neither opens, deepens nor closes a period reads no clock; a clock never set leaves the instants unnamed; a portfolio built without a clock still refuses |
| `test_both_account_models_feed_it_the_same_way` | a margin position and a spot holding move the periods alike (both account models) |
| `test_a_close_can_recover_a_period` | a position close samples the curve past `sample_equity` and can recover a period |
| `test_the_runtime_the_carried_and_the_served_period_hold_the_same_fields` | the runtime record, the carry-over and the served row of a period hold the same fields — the carried model ignores an unknown key, so a field missing there would vanish at a restart |
| `TestTheyContinueAcrossARestart` | the kept periods and the open one come back through the carry-over; the open one recovers against the restored peak; a record from before starts empty; a reset forgets them |

The served row — ranked and keyed by `rank` — is held by `tests/framework/reporting/test_portfolio_report.py`.

## Running

```bash
pytest tests/framework/test_account_drawdown_periods.py -v --tb=short
```
