# Trading Day Anchor Tests

`tests/framework/test_trading_day_anchor.py` — where a market's trading day flips
(`python/framework/utils/trading_day_anchor.py`, #476). Runs under the synthetic
`framework/_root` suite.

## Why this file exists at all

Three places used to answer "which day is it" independently, all of them from the tick stamp
and all of them at midnight UTC: the session-log rotation, the daily-loss baseline (#314) and
the record seal. Midnight is right for crypto by coincidence and wrong for forex, whose day
flips at the swap rollover — and deriving it from the TICK means a feed that goes silent across
the boundary misses it entirely.

Those callers now share one answer. What their own tests show is that each caller behaves; this
file pins what the answer IS, including the two cases a caller never produces on purpose.

## What Is Tested

### `TestCryptoAnchor`

00:00 UTC, so a trading day and a calendar day coincide. The boundary case is the one that
would be invisible in production: an instant one second before midnight belongs to the day
before, and mislabelling it moves a whole fragment.

| Test | Description |
|------|-------------|
| `test_the_calendar_day_is_the_trading_day` | midnight, mid-day and 23:59:59 — parametrized, all one label |
| `test_one_second_before_midnight_is_the_day_before` | the off-by-one, stated rather than implied |
| `test_the_boundary_is_midnight` | the opening instant itself |

### `TestForexAnchorAcrossDst`

17:00 America/New_York. The reason this class exists is that the UTC instant MOVES with
daylight saving while the label does not — a fixed offset would put every winter fragment an
hour out, and nothing would fail.

| Test | Description |
|------|-------------|
| `test_summer_boundary_is_21_utc` | 17:00 New York in EDT |
| `test_winter_boundary_is_22_utc` | the same wall clock in EST, an hour later in UTC |
| `test_the_day_flips_at_the_rollover_not_at_midnight` | either side of the boundary, in both seasons — parametrized |
| `test_midnight_utc_belongs_to_the_day_that_opened_the_evening_before` | the case that separates this module from a plain UTC date |

### `TestResolution`

Which anchor a broker gets. These build the market rules rather than reading the merged config,
deliberately: an operator override in `user_configs/market_config.json` is legitimate — shifting
the crypto anchor a few minutes ahead is how the live boundary is rehearsed — and a test
asserting the operator's current VALUES would go red for that, which is the wrong reason. One
smoke test is all that touches real config.

| Test | Description |
|------|-------------|
| `test_a_declared_anchor_wins` | `trading_day_anchor` is read first |
| `test_without_one_the_swap_rollover_answers` | the fallback, so the forex instant exists once in the config |
| `test_a_declared_anchor_beats_a_swap_rollover_that_disagrees` | a market may charge swap and flip its day elsewhere |
| `test_both_shipped_brokers_resolve_to_something` | the smoke half — parametrized over `kraken_spot` and `mt5`, asserts only that neither is anchorless |
| `test_a_market_with_neither_is_refused_rather_than_defaulted` | `TradingDayAnchorMissingError` instead of a midnight-UTC default |

## What Is NOT Tested Here

The callers. That the session log actually rotates on the boundary, and that the daily-loss
baseline resets there, belongs to `tests/autotrader/loop_cadence/` and
`tests/autotrader/safety/` — this file answers only what the boundary IS.

A local time that does not exist on a spring-forward date resolves to the shifted instant
rather than raising. Neither anchor this project configures falls in such a gap, so the case is
recorded here rather than asserted.

## Running

```bash
pytest tests/framework/test_trading_day_anchor.py -v
```
