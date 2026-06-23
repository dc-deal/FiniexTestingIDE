# Market Calendar / Swap-Rollover Tests

`tests/framework/market_calendar/` — the swap-rollover + DST calendar apparatus the
overnight-swap accrual (#365) relies on: `MarketCalendar` rollover helpers,
`time_utils` DST conversion, and the `MarketClock` awareness layer.

## test_swap_rollover.py (11 tests) — pure calendar/time helpers

| Test | Description |
|------|-------------|
| `test_mt5_weekday_mapping` | MT5 weekday (Sun=0) → Python (Mon=0) conversion |
| `test_local_to_utc_winter_est` | 17:00 NY (EST) → 22:00 UTC |
| `test_local_to_utc_summer_edt` | 17:00 NY (EDT) → 21:00 UTC (DST shift) |
| `test_iter_two_normal_nights` | window crosses Mon + Tue rollovers (×1 each) |
| `test_iter_triple_wednesday` | Tue ×1 + Wed ×3 (triple) = 4 swap-days |
| `test_iter_skips_weekend` | Fri + Mon only — Sat/Sun carry no rollover |
| `test_iter_empty_window` | start == end → no crossings |
| `test_next_rollover_normal` | next rollover instant + multiplier 1 |
| `test_next_rollover_triple` | next rollover on the triple weekday → multiplier 3 |
| `test_next_rollover_skips_weekend` | Fri post-close → next is Monday |
| `test_next_market_close_is_friday` | next weekend close is the upcoming Friday |

## test_market_clock.py (6 tests) — `MarketClock` awareness

| Test | Description |
|------|-------------|
| `test_forex_time_to_next_rollover` | forex: timedelta to the next rollover |
| `test_forex_triple_on_wednesday` | Wed → next rollover is triple |
| `test_forex_not_triple_on_monday` | Mon → next rollover is not triple |
| `test_forex_time_to_market_close_positive` | forex: positive time to weekend close |
| `test_forex_is_weekend_ahead` | weekend close within / outside a look-ahead window |
| `test_crypto_has_no_swap_or_weekend` | crypto spot: all awareness queries → None/False |
