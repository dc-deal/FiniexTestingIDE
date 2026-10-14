# Booking Period Parity Tests

A trading day's booking period is sealed by the first tick past the market's boundary, and the
seal reads the account value AFTER that tick was applied: the boundary tick's value is the
period's `final_equity`. That value therefore has to be inside the period's equity band too —
observed before the seal, in both pipelines and in that order. Observed after the seal, the
closing value would sit outside the band it closes, a drop on the boundary tick would land in
neither day's deepest decline, and the band figures would differ between a live session and the
backtest of the same window by construction — the comparison the parity proof is made of.

**Location:** `tests/parity/test_booking_period_parity.py`

Not covered here: how a period is derived from its trades and what it reconciles against — that
is `docs/tests/framework/reporting_tests.md` (the recorder and the booking-periods report).

## What It Validates

| Test | Focus |
|------|-------|
| `test_both_pipelines_book_the_day_the_same_way` | The trading day's period and the one after it agree field by field — reason, number, opening instant, trade count, net P&L, closing and opening equity, the band's high and low, the deepest decline — and the day closes at midnight in both |
| `test_the_day_holds_its_closing_value_and_the_gap_on_its_boundary` | In each pipeline on its own: the closing value lies inside its own band, and the day sealed on the gap carries that gap in its deepest decline |

Both run in both account models (margin and spot).

## Fixture Design

Flat BTCUSD ticks at one per second, shifted so tick 300 lands on midnight UTC — the trading-day
boundary of `kraken_spot`, a crypto market. That tick alone drops by 2,000 USD; the next one is
back. A `DeterministicProbe` opens 0.01 BTC long at tick 100 and closes it at tick 600, so the
position is held through the boundary and the gap moves the account by 20 USD exactly there.

Flat everywhere else on purpose: the simulation fills a market order one tick after the
AutoTrader loop does, and a constant price makes that difference invisible — whatever differs is
the booking order. The AutoTrader loop is built the way a session builds it, with its risk
baseline tracker, because the booking period's valuation rides the same per-tick read.

## How to Run

```bash
python -m pytest tests/parity/test_booking_period_parity.py -v
```
