# Booking Period Parity Tests

A trading day ends at its boundary, before the first tick at or after that instant is applied
(#476). The seal reads the account as the day's last tick left it and is stamped with the boundary;
the tick that crosses it — its fills, its trades, its move — opens the new day. Both pipelines
have to cut there, or the day rows of a live session and of the backtest of the same window differ
by construction — the comparison the parity proof is made of. And the session's daily loss limit
has to cut there too, or a gap on the boundary tick counts against neither day's limit.

**Location:** `tests/parity/test_booking_period_parity.py`, its loops built by
`tests/shared/booking_day_harness.py`

Not covered here: how a period is derived from its trades and what it reconciles against — that
is `docs/tests/framework/reporting_tests.md` (the recorder and the booking-periods report).

## What It Validates

| Test | Focus |
|------|-------|
| `test_both_pipelines_book_the_day_the_same_way` | The trading day's period and the one after it agree field by field — reason, number, opening instant, trade count, net P&L, closing and opening equity, the band's high and low, the deepest decline — and the day closes at midnight in both |
| `test_the_gap_on_the_boundary_tick_belongs_to_the_new_day` | In each pipeline on its own: the day that ended closes at midnight with the value its last tick left and no part of the gap; the new day opens at that value and carries the gap in its deepest decline; each closing value lies inside its own band |
| `test_the_daily_loss_limit_cuts_the_day_where_the_booking_period_does` | The AutoTrader's safety days are the booking period's trading days; the new day's baseline is the value the seal read, stamped at midnight, and the gap is the new day's worst loss |
| `test_a_boundary_between_two_ticks_is_cut_at_its_instant` | No tick within five seconds of midnight: both pipelines agree field by field, the day closes at midnight rather than at the tick that noticed it, and that tick's gap is the new day's |
| `test_every_trading_day_a_gap_crosses_gets_its_period` | A feed silent for a day: both pipelines agree field by field, the day nothing was heard from has its period — no tick, no trade, the value the seal before it read, a band that never moved — and the AutoTrader's daily loss limit keeps a day for it with no loss |
| `test_a_stop_the_boundary_tick_breaches_closes_in_the_new_day` | The gap breaches the position's stop: in each pipeline the close is the new day's and the day that ended books none. Where the stop fills is not compared — the simulation fills a breached level at the level, the AutoTrader at the market a round trip later (#629) |
| `test_a_fill_between_the_last_tick_and_the_boundary_is_the_ending_day_s_loss` | AutoTrader only: the close goes out on the last tick before midnight and fills on an idle heartbeat before it, where no tick evaluates the daily loss limit. The day files the loss the seal's reading shows, stamped with the boundary, and the new day starts after it |

All run in both account models (margin and spot).

## Fixture Design

Flat BTCUSD ticks at one per second, shifted so tick 300 lands on midnight UTC — the trading-day
boundary of `kraken_spot`, a crypto market. That tick alone drops by 2,000 USD; the next one is
back. A `DeterministicProbe` opens 0.01 BTC long at tick 100 and closes it at tick 600, so the
position is held through the boundary and the gap moves the account by 20 USD exactly there.

Flat everywhere else on purpose: the simulation fills a market order one tick after the
AutoTrader loop does, and a constant price makes that difference invisible — whatever differs is
the booking order. The AutoTrader loop is built the way a session builds it, with its risk
baseline tracker, because the daily loss limit rides the same per-tick read.

The harness varies three things for the boundaries no tick lands on: `quiet_seconds` removes the
ticks around midnight, so the gap moves to the first tick after it; `days_skipped` moves every
tick from the boundary tick on that many days later, so one gap crosses several boundaries; and
`stop_loss` gives the position a stop and holds it to the end of the feed, so the stop is what
closes it; `hold_ticks` sets how long the position is held. The AutoTrader loop can also end
with idle heartbeats instead of an end-of-feed marker (`idle_heartbeats`), which the
loop-cadence tests use to notice a boundary on a heartbeat; a test that fakes the wall clock
those heartbeats read decides on which side of midnight they fall.

## How to Run

```bash
python -m pytest tests/parity/test_booking_period_parity.py -v
```
