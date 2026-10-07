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
[data_storage_layout.md](data_storage_layout.md)).

## The periods

| Period | Anchored to | What it resets or produces | Pipelines |
|---|---|---|---|
| Tick | every tick | The equity sample: account peak, account drawdown, drawdown percentage | simulation + AutoTrader |
| Swap rollover | 17:00 `America/New_York`, resolved per date (DST-aware) | One signed swap fee per rollover crossed, tripled on the configured weekday | both — MARGIN only; a no-op for spot and for markets without swap |
| Trading day | the market's own anchor: crypto 00:00 UTC, forex the swap rollover — resolved per date, DST-aware | A fresh `DAY_START` baseline; the per-day worst-loss counters return to zero; the closing day is filed as one record | AutoTrader only |
| Log rotation | the SAME anchor, read from the canonical clock | A new session log file. Fires on the heartbeat too, so a silent feed over the boundary still rotates |  AutoTrader only |
| Algo state | hybrid tick / second cadence | What the algo chose to remember. Discarded once older than `max_age_trading_days` | AutoTrader |
| Cold-start state | boot, shutdown, and a structural change of the open book; excursion extrema on a tick cadence | Session keys, the position-counter high-water mark, the open position book. Never discarded for age | AutoTrader |
| **Booking period** | the SAME trading-day anchor, read from the canonical clock | One sealed `BookingPeriod` per run unit: the period's realised figures, its control total and its own equity band. Fires on the heartbeat too, so a quiet feed over the boundary still books | both — AutoTrader per session, simulation per SCENARIO |
| Session end | once, when the run ends | The final equity sample, the report, and the ledger rows — one per booking period, not one per session | both |

**Both of those used to read midnight UTC off the TICK stamp, and both were corrected on
2026-09-21 (#476).** Midnight is right for crypto by coincidence and wrong for forex, whose day
flips at the swap rollover; and a tick-derived boundary is missed entirely when the feed goes
quiet across it, because only the heartbeat advances the clock in an illiquid gap. One owner
now answers it — `framework/utils/trading_day_anchor.py` — and the anchor is config, with
a hard error where a market declares none.

**The two are still not simultaneous, and that is deliberate:** the log rotation also runs on the
heartbeat, while the risk day reaches the boundary only on the next real TICK, because at spot
the baseline needs a mark price and a heartbeat carries none. The lateness is self-limiting —
nothing can move the account until a tick arrives, and that is the same tick which resets the
denominator.

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
