# Accounting Periods — Which Clock Resets What

A live session runs for thirty days without stopping, and several different things inside it
reset on several different schedules. Two of them are called "the day" and they are not the same
day: the broker books swap at 17:00 New York, while the risk baseline rolls at midnight UTC. A
reader who assumes one day boundary will misread both.

This page is the overview. It says what each period is anchored to, what it resets, and which
pipeline has it. It does NOT explain the swap arithmetic (that is
[swap_cost_model.md](../trading_realism/swap_cost_model.md), including DST and triple swap) and it
does not describe the stores themselves (that is
[data_storage_layout.md](data_storage_layout.md)).

## The periods

| Period | Anchored to | What it resets or produces | Pipelines |
|---|---|---|---|
| Tick | every tick | The equity sample: account peak, account drawdown, drawdown percentage | simulation + live |
| Swap rollover | 17:00 `America/New_York`, resolved per date (DST-aware) | One signed swap fee per rollover crossed, tripled on the configured weekday | both — MARGIN only; a no-op for spot and for markets without swap |
| Risk day | midnight UTC, read from the TICK | A fresh `DAY_START` baseline; the per-day worst-loss counters return to zero; the closing day is filed as one record | live only |
| Log rotation | the tick's date | A new session log file. Deliberately independent of the risk day | live only |
| Algo state | hybrid tick / second cadence | What the algo chose to remember. Discarded once older than `max_age_trading_days` | live |
| Cold-start state | boot, shutdown, and a structural change of the open book; excursion extrema on a tick cadence | Session keys, the position-counter high-water mark, the open position book. Never discarded for age | live |
| Session end | once, when the run ends | The final equity sample, the report, one ledger row | both |

## Two days, and why they differ

The swap day belongs to the **broker**: it is the instant the venue books overnight financing,
expressed in the venue's own local time, so it moves with daylight saving.

The risk day belongs to **us**: it is the denominator a daily loss limit is measured against, and
it is struck at midnight UTC on whichever tick arrives first after the boundary. Reading the date
from the tick rather than from the wall clock is what makes a replay and a live session answer the
same way.

The approximation this leaves is written down at the code: a market whose trading day rolls at
17:00 New York sees its daily limit reset in the middle of its session until the market-anchored
boundary event exists (#476, #415).

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
