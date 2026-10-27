# Accounting Periods — Which Clock Resets What

An AutoTrader session runs for thirty days without stopping, and several different things inside it
reset on several different schedules. Two of them are called "the day", and whether they fall on
the same instant depends on the market: the broker books swap at 17:00 New York, and our own
trading day is anchored to that same rollover on forex while crypto rolls at 00:00 UTC. A reader
who assumes one day boundary will misread both.

This page is the overview. It says what each period is anchored to, what it resets, and which
pipeline has it. It does NOT explain the swap arithmetic (that is
[swap_cost_model.md](../trading_realism/swap_cost_model.md), including DST and triple swap) and it
does not describe the stores themselves (that is
[data_storage_layout.md](data_storage_layout.md)). How the rows read once they are written — which
numbers add up, over one account and over several — is
[Reading the Results Ledger](../user_guides/results_ledger_guide.md).

## The periods

| Period | Anchored to | What it resets or produces | Pipelines |
|---|---|---|---|
| Tick | every tick | The equity sample: account peak, account drawdown, drawdown percentage | simulation + AutoTrader |
| Swap rollover | 17:00 `America/New_York`, resolved per date (DST-aware) | One signed swap fee per rollover crossed, tripled on the configured weekday | both — MARGIN only; a no-op for spot and for markets without swap |
| Trading day | the market's own anchor: crypto 00:00 UTC, forex the swap rollover — resolved per date, DST-aware; only the days the market calendar trades | A fresh `DAY_START` baseline, struck at the boundary from the value the booking period sealed with; the per-day worst-loss counters return to zero; the closing day is filed as one record | AutoTrader only |
| Log rotation | the SAME day, read from the booking period | A new session log file, right after the seal, so the line that seals a day stays in that day's file. Fires on the heartbeat too, so a silent feed over the boundary still rotates |  AutoTrader only |
| Algo state | hybrid tick / second cadence | What the algo chose to remember. Discarded once older than `max_age_trading_days` | AutoTrader |
| Cold-start state | boot, shutdown, and a structural change of the open book; excursion extrema on a tick cadence | Session keys, the position-counter high-water mark, the open position book. Never discarded for age | AutoTrader |
| **Booking period** | the SAME trading-day anchor, checked before each event is applied — every trading day of the market calendar, a day without a tick included | One sealed `BookingPeriod` per run unit: the period's realised figures, its control total, its tick count, its own equity band and the orders it handled — counted inside the period where the run counts them, so the periods add up to the run's execution statistics. Stamped at the boundary, whichever event noticed it. A live session books it into the ledger as it seals and tells its operator how the day closed — the same period, rendered for Telegram ([Unattended Operation](../autotrader/unattended_operation.md)) | both — AutoTrader per session, simulation per SCENARIO |
| Session end | once, when the run ends | The final equity sample, the report, and the ledger rows written whole once more — one per booking period, not one per session, the run-end columns filled | both |

**Both of those used to read midnight UTC off the TICK stamp, and both were corrected on
2026-09-21 (#476).** Midnight is right for crypto by coincidence and wrong for forex, whose day
flips at the swap rollover; and a tick-derived boundary is missed entirely when the feed goes
quiet across it, because only the heartbeat advances the clock in an illiquid gap. One owner
now answers it — `framework/utils/trading_day_anchor.py` — and the anchor is config, with
a hard error where a market declares none.

**Since #476 all three cut the day at the same instant.** The booking period owns the day: the
risk day rolls on its seal, from the value the seal read — at spot valued at the last tick's price —
and the log rotates right after it. The tick that crosses the boundary therefore counts against the
new day's loss limit; before, the risk day closed before that tick and took its new baseline after
it, so the tick's move was in no day's record — on a forex open, the whole weekend's gap. The day
that ended also takes the seal's reading into its worst loss, so a fill drained between its last
tick and the boundary is that day's: [Where the daily loss limit's day
ends](../autotrader/autotrader_capital_and_safety.md#where-the-daily-loss-limits-day-ends).

## Where a trading day ends

A trading day ends at its boundary, **before** the first event at or after that instant is applied.
That event — a tick, or a heartbeat that resolves a fill — belongs to the new day: its fills, its
trades and its valuation. The boundary is checked at the start of every pass of both loops, against
the instant cached for the running day, so the ordinary tick pays one comparison.

- **Stamped with the boundary.** `closed_at` is the boundary instant, not the moment a tick or a
  heartbeat noticed it, and the next period opens there. A backtest without heartbeats and a live
  session whose heartbeat crossed the boundary 300 ms late book the same span.
- **Read as the last event left it.** The seal reads the account before the crossing event, folds
  that reading into the closing band, and opens the next band at it — so a fall between the boundary
  and the next tick is the new day's decline, and a closing value always lies inside its own band.
- **Every trading day, and only those.** The market calendar decides, asked about the date a day
  trades FOR — Monday for the forex session that opens Sunday 17:00 New York. Crypto books every
  day. A forex market books no Friday or Saturday session and no holiday; the period after them
  opens where the last trading day ended, so no stretch of the run falls between two periods.
- **A day nobody heard from still gets its row.** An instant several boundaries past the last one —
  a gap in the data, a feed back after days — seals the day that ran and then one period per
  trading day in between: `tick_count` 0, nothing realised, the valuation it was left at. A metric
  over the rows can leave those out. An AutoTrader session books the days one instant seals
  together in one ledger write, and the operator hears the first in full and the rest in one
  message.
- **The session's end is inside its last period.** No period opens at a session's end, so the last
  period's trade window includes its end instant.

## Two days, and when they coincide

The swap day belongs to the **broker**: it is the instant the venue books overnight financing,
expressed in the venue's own local time, so it moves with daylight saving.

The trading day belongs to **us**: it is what a log file, a daily loss limit and a booking period
are keyed by. It is resolved from the market's configured anchor, and that anchor FALLS BACK to
the swap rollover where a market has one — so on forex the two days are the same instant by
construction, while crypto, which books no swap, stands on its own at 00:00 UTC.

They used to be two answers. Both the log rotation and the risk day read midnight UTC off the
TICK stamp, which is right for crypto by coincidence and wrong for forex by most of a session: a
daily limit that reset in the middle of the trading day, and a boundary that a quiet feed could
cross unnoticed. One owner answers it now, from the canonical clock.

## The three levels, and why they are named after a ledger

The booking period is not a reporting convenience. It is the middle level of ordinary
double-entry bookkeeping, and the project uses those names on purpose:

| Level | Classical name | Here |
|---|---|---|
| individual bookings, chronological | **Grundbuch** (journal) | the `TradeRecord`s |
| period summaries per account | **Hauptbuch** (ledger) | the booking-period rows |
| figures over many periods | **Abschluss** (closing) | a deployment's total, a drawdown over a month, Sharpe / Calmar |

Each level is believed because it can be **recomputed** from the one below it, and each carries
a **control total** so it can disprove itself — `total_trades` says how many records a
period's figures came from, so a reader who re-derives them and gets a different count knows
the row is wrong rather than merely surprising. It has to be a figure the row did NOT produce
in the same breath: a second column holding the same `len(rows)` reads as an audit and is none,
which is why the one that did exist was removed. That is the whole reason the construction has
survived for centuries, and it is the rule this project states in its own words: every money
figure stays re-derivable from the records it came from.

The practical consequence is that the third level costs nothing to add. A Sharpe ratio, a Calmar
ratio, a monthly drawdown are not another layer of bookkeeping — they fall out of the period
summaries the way a company's annual figures fall out of its daily closings.

### Why a simulation books per SCENARIO and not per run

A run's scenarios cover DIFFERENT windows — measured 2026-09-21 over the shipped sets, a
40-scenario robustness run has 40 distinct ones, and a 5-scenario set walks five consecutive
two-hour windows of one day. "Day 1 of the run" is therefore not a thing; only "day 1 of this
scenario" is, and each scenario counts its periods from 1.

An AutoTrader session is the same rule with one unit. That is not a coincidence: the reporting
pipeline already models both pipelines as a list of RUN UNITS (a backtest: N scenarios; an
AutoTrader session: one), and
the booking period hangs off that unit rather than off the run.

No count is carried into a backtest. An AutoTrader session inherits its period number from the
cold-start carry-over so a restarted deployment has one unbroken sequence; a scenario has no
predecessor to inherit from.

### The one thing a reader gets wrong

**A trade belongs to the period it was CLOSED in.** Realised is booked. So a position opened on
Monday and closed on Tuesday puts its ENTIRE result on Tuesday, although it worked overnight:

```
            Period 1                     │            Period 2
            Mon 00:00 ──────────── Tue 00:00 ──────────── Wed 00:00
                                          │
T1          ╞═══════════╡                 │   wholly inside 1     →  booked in period 1
T2                   ╞════════════════════╪═══╡   CROSSES         →  booked in period 2
T4                                        │        ╞════════════▶  still open  →  valued, not booked
```

That is correct bookkeeping and it is not the whole story about Monday. Which is why a period
carries its **equity band** beside its realised figures: the two differ by exactly the
unrealized movement across the boundary. Flow is derived from records; stock is read at an
instant. A reader who takes one for the other will find a day whose booked result and
whose account movement disagree, and conclude that something is broken.

### What a period row carries beyond its result

- **What it opened with.** `opening_equity` is the previous period's closing value, read at the
  same instant, or — for a unit's first period — its first observed account value. It is stamped
  where it is known and never computed as `final_equity − net_pnl`: `net_pnl` is realised, while
  the equity also values what is still open, so that difference is off by exactly the unrealized
  movement described above.
- **Its own close, inside its band — when a tick seals it.** A period sealed by the first tick
  past the boundary reads the account value after that tick: that value is the period's
  `final_equity`, and both pipelines observe it into the band BEFORE sealing, so the closing value
  lies inside the band it closes and a drop on the boundary tick is in the closing day's deepest
  decline. Pinned as parity in `tests/parity/test_booking_period_parity.py`. A period sealed by an
  idle pass, or at the session's end, reads the account value without a fresh observation: a fill
  that resolved since the last tick is in its `final_equity` but not in its band.
- **Its costs, split.** `commission_cost`, `swap_cost` and `spread_cost`, summed over the same
  trades `total_fees` is — the ones the period closed. Commission and swap add up to `total_fees`;
  the spread is measured against the mid and stands beside the fees, never inside them. A swap
  accruing on a position that is still open is therefore not in any period until it closes — it
  belongs to the open position, not to the closed trades.
- **Each unit's total.** The booking-periods report folds each unit's periods into one row by the
  same declared reductions the ledger uses: rates are rebuilt from their summed components, the
  drawdown comes with the period that owns it, and a streak is not foldable at all. A unit is one
  account, so its closing equity is defined there — and its opening is its FIRST period's, or
  unknown when that period did not record one; a later period's opening is a different instant.

**Over several units there is no single equity.** A backtest's scenarios each trade their own
balance. Their closing equities add up to a TOTAL, which the report serves as one
(`total_final_equity`) beside the capital it started from; a figure that means one account's value
is null wherever it would have to speak for several.

## What does not reset

The **session** references do not roll with the day. The safety baseline and its high-water mark
survive a restart on purpose, because a drawdown that restarted with the process would describe a
day rather than a month. The daily baseline is the opposite and equally deliberate: it is NOT
persisted, because a daily limit that survived a restart would carry yesterday's loss into today,
which is the opposite of what "daily" means.

## Account model

Only the swap row depends on it — spot and crypto have no overnight financing, so the accrual is a
no-op there. Everything else measures the account value and is the same in both models.

One consequence is worth stating because it is easy to get backwards: the cold-start state matters
MORE in spot. A spot holding is a balance, and the venue cannot describe it as a position, so it
survives a restart only because we wrote it down. A margin position sits in the market and comes
back from there.
