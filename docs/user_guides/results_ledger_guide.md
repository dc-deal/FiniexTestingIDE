# Reading the Results Ledger — One Account, and Several

Open the results of a backtest and the ledger hands you more rows than you ran anything: a run of
two scenarios over three days comes back as five rows, a sweep as hundreds. Add them up the
obvious way and you get numbers that look right and are not: a profit factor that is the average
of three profit factors, a balance no account ever held, euros added to dollars. This guide says
what one row is, which of its numbers may be added and which may not, and what changes when the
rows belong to several accounts rather than one.

It follows the order in which the questions come up the first time you look at the ledger, and each
section answers one of them.

**Not here:** what each field of an HTTP answer means —
[Booking periods](../consumer/booking-periods.md), [Run summary](../consumer/run-summary.md) and
[What makes one row unique](../consumer/row-keys.md) own that. How the sessions of one bot become
one history across its restarts is [Reading a bot across its restarts](deployment_ledger_guide.md);
how the periods are cut and sealed is [Accounting Periods](../architecture/accounting_periods.md);
how a multi-window robustness set is built and judged is
[Robustness Validation](robustness_validation_guide.md).

---

## Where you meet the ledger

Every place that shows results per period, per run or per sweep reads the same rows:

| You look at | Where |
|---|---|
| one run's periods | the `BOOKING PERIODS` section of the run's summary, or `GET /api/v1/reports/runs/{run_id}/booking-periods` |
| a sweep's ranking | `python python/cli/optimization_cli.py report <sweep_id>`, or `GET /api/v1/sweeps/{sweep_id}` |
| a bot's history | `python python/cli/run_index_cli.py deployments`, or `GET /api/v1/deployments/{deployment_id}/booking-periods` |

So what this guide says about a row holds on every one of these surfaces.

## A row is one trading day of one account

**Every row is one booking period of one account.** An account is one scenario of a backtest, each
started from its own capital, or the one session of an AutoTrader run. A booking period ends at
the market's trading-day boundary — 17:00 New York for forex, midnight UTC for crypto — or when
the run ends.

A run therefore has several rows for exactly two reasons: it has several accounts, or an account
ran across a trading-day boundary.

```
a backtest of two scenarios
  scenario A   │ day 1 │ day 2 │ day 3 │    → 3 rows
  scenario B   │ day 1 │ day 2 │            → 2 rows (its window ends sooner)
                                            = 5 rows, one run
```

The currency is never a reason for a row of its own. An account trades in one account currency,
so every period of it is in that currency.

**All rows of a run are written together, when the run ends.** A run that is killed or crashes
before its end has sealed periods but no rows; its run header still shows that it started. What
that means for a bot is in
[A session that never reached its close](deployment_ledger_guide.md#a-session-that-never-reached-its-close).

## What makes a row unique, and where the currency comes in

A row is identified by the run, the account's unit name and the period number. Nothing else is
needed: a scenario set refuses two scenarios with the same name, and a session has one account.

The currency enters only when rows are **folded** into one line. One line per run is all of the
run's rows combined — and those may belong to accounts in different currencies, which cannot be
added. So every fold keeps the currencies apart:

| You want | The rows are keyed by |
|---|---|
| each period on its own | run · unit name · period number |
| one line per run (a sweep's ranking, a deployment's sessions) | run · currency |
| one line per deployment | deployment · currency |

The first key says **which row** it is. The other two say **which rows may be added together**.
Every answer that serves a list says which key holds it — see
[What makes one row unique](../consumer/row-keys.md).

## Three kinds of number on a row

A row carries three kinds of figure, and mixing them up is the most common misreading:

- **What happened during the period** — `net_pnl`, `total_fees`, `trade_count`. Only this period's,
  and only **closed** trades: `net_pnl` is realised. A trade belongs to the period it was closed
  in, even when it was opened days earlier.
- **Readings at the period's edges** — `opening_equity`, `final_equity`, `unrealized_pnl`,
  `open_position_count`. A reading is true at one instant. `opening_equity` is the previous
  period's `final_equity`.
- **What the account carries from its start** — `account_max_drawdown` and its share. It repeats
  down the column until a deeper fall moves it, because a fall that runs across a boundary would
  otherwise be lost. The period's own decline is `max_drawdown`, beside it.

`net_pnl` is therefore not carried forward. Your balance per day is the `final_equity` column, read
down the periods.

### A worked example

An account opens with 10,000.00 (the numbers are invented):

```
         opening_equity   net_pnl   unrealized_pnl   final_equity
day 1       10,000.00     −120.00        −30.00         9,850.00
day 2        9,850.00      +60.00          0.00         9,940.00
```

- **Day 1:** two trades close for −120.00; a third is still open at the boundary and is valued at
  −30.00. 10,000.00 − 120.00 − 30.00 = 9,850.00.
- **Day 2:** the third trade closes at −10.00 and another wins +70.00, so `net_pnl` is +60.00. The
  whole −10.00 is booked on day 2, the day it closed, and the −30.00 it was valued at on day 1 is
  no longer open: 9,850.00 + 60.00 − (−30.00) + 0.00 = 9,940.00.

The relation that ties the rows together is **closing = opening + realised + the change in what is
open**. It is how you tell a wrong row from a surprising one. Measured 2026-10-19 on a mock session
and a spot backtest, it held to the cent.

It does **not** hold for a spot account that also holds the coin it trades — a backtest seeded
with, say, some ETH beside its USD. That holding is part of the equity and moves with the price
without a single trade, so a quiet day can still move `final_equity`. The same 2026-10-19
measurement on such an account missed by 3.21 and 6.91 on its two days.

It is the bank statement you know: old balance, movements, new balance, and the new balance is
the old one on the next statement. The bookkeeping behind it — journal, ledger, closing — is
explained in
[Accounting Periods](../architecture/accounting_periods.md#the-three-levels-and-why-they-are-named-after-a-ledger).

## Every column says how it adds up

When rows are folded, each column follows its own rule. The rule is declared once with the
ledger, so every surface folds the same way and none of them works it out for itself:

| Kind | Columns | How they combine | Why |
|---|---|---|---|
| what happened | net P&L, fees, trades, gross profit, gross loss | added | the rows split them without overlap |
| ratios | profit factor, win rate, expectancy | recomputed from the added parts | the average of three profit factors is not a profit factor |
| the decline | the account's drawdown and its share | the deepest | adding them counts one fall once per day it lasted |
| readings of one account | final equity, unrealized P&L, open positions | the latest — for **one** account only | a balance is a moment, not an amount to add |
| what ran | strategy, parameters, code version | taken as they stand | they describe the run; they are never an amount to add |

The two days above, folded: `net_pnl` −120.00 + 60.00 = −60.00, `final_equity` the latest,
9,940.00, and 10,000.00 − 60.00 = 9,940.00 again. A profit factor over the two days is gross
profit divided by gross loss, both summed, never the mean of the two daily ones.

A company's books work the same way: the year's profit and loss is the sum of the months, and the
year-end balance sheet is the last one, not a sum of twelve.

A few columns cannot be folded at all — a winning streak can cross a boundary, and per-period
order counts are not recorded. They are null on a folded line; see
[What the fold cannot answer](../consumer/sweeps.md#what-the-fold-cannot-answer).

## One account over time, several accounts side by side

Rows are folded in two different situations, and one rule changes between them.

**One account over time** — a scenario's days, a deployment's sessions. Every rule above applies,
readings included: the latest balance is the account's balance.

**Several accounts side by side** — the scenarios of one backtest, each with its own capital.
What happened still adds up and the ratios are still recomputed, but a reading of one account has
no answer any more: the closing balance of the last scenario is not the balance of the run. Those
fields are null on such a line, and the sum of the closing balances is served under a name of its
own, [`total_final_equity`](../consumer/booking-periods.md#total_final_equity), because no account
ever held it.

Why fold several accounts at all? To compare. A sweep runs every parameter combination over the
same windows, and the question is which combination did best — which needs one figure per
combination, not one per window:

```
                 window 1   window 2   window 3   │  all three
combination A      +50        −30        +10      │    +30
combination B      +80        −90         +5      │     −5

ranked row by row:          B's +80 comes first   ← one good window of B
ranked per combination:     A before B            ← what the sweep asked
```

Asset managers face the same question with real money: one strategy run in several separate client
accounts. What the strategy earned across them is a sensible figure; what "the" balance is has no
answer. The industry calls such a group of accounts a **composite**, the term of the Global
Investment Performance Standards (GIPS, CFA Institute).

## What the ledger keeps apart today

When it folds the accounts of one run, the ledger:

- **keeps the account currencies apart** — always, on every fold;
- **leaves every reading of one account empty** — the final equity, the unrealized P&L, the open
  positions.

It does **not** check whether the scenarios of a run ran the same strategy under the same
conditions. A scenario set can give one scenario its own strategy, its own simulator settings or a
stress test, and the run is folded as before. Its sum still checks the books — the periods add up
to the run's own figures — but it is then not the result of one strategy. Neither does it keep
spot and margin accounts apart when they share a currency. If you read a run's total as what one
strategy earned, check first that its scenarios ran the same one.

## Further reading

- CFA Institute, *Global Investment Performance Standards (GIPS) for Firms* (2020), with its
  *Handbook* — when separate accounts may be presented as one result.
- Carl R. Bacon, *Practical Portfolio Performance Measurement and Attribution*, 3rd ed. (Wiley,
  2023) — how a return over several accounts and several periods is computed.
