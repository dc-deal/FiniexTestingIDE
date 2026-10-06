# Portfolio

A run ends and the question is what it did to the money. Rebuilding that from the trades means
summing every fill and every fee yourself while keeping each account apart — and the mistake that
cannot be undone further down is the easy one: two accounts added into a figure no account ever
held. This section is the run's headline. One row per unit carrying that unit's whole projection,
and one row per account currency folding those up without ever crossing a currency.

**Routes**

```
GET /api/v1/reports/runs/{run_id}/portfolio
```

**What is not here:** the trades themselves ([trade history](/api/v1/docs/trade-history)), the
money booked per period ([booking periods](/api/v1/docs/booking-periods)), and the cross-section
KPIs a sweep ranks on ([run summary](/api/v1/docs/run-summary)). A backtest's rich per-currency
detail — balances, cost split, execution and pending counts — is
[aggregated portfolio](/api/v1/docs/aggregated-portfolio).

## Two lists, two keys

```json
{ "keys": { "units": ["name"], "aggregates": ["currency"] } }
```

`units` is one row per run unit: a scenario in a backtest, the session in a live run.
`aggregates` folds the units that share one account currency into a single row. See
[row keys](/api/v1/docs/row-keys).

A unit's `name` is a join and is meant as one. The same value identifies that unit in the roster
on [scenario details](/api/v1/docs/scenario-details) and among the absent units on
[run summary](/api/v1/docs/run-summary), so a selection carried by it narrows every section at
once.

## The body names its own run

`run_id` is in the response, not only in the URL you asked with. Two different sweep combinations
produce byte-identical portfolio bodies — measured — so a consumer handed the wrong one has
nothing in the payload to notice it by. The route is not proof; the body is. Assert on it.

## What one unit row says

| Field | Meaning |
|---|---|
| `name` · `symbol` · `currency` | which unit, which instrument, which account currency |
| `data_broker_type` | the broker whose ticks the unit read |
| `data_sentiment_type` | the unit's [signal](/api/v1/docs/signal) source, where it had one |
| `broker_name` · `spot_mode` | the venue the unit traded at, and whether it ran as a spot account |
| `total_trades` · `winning_trades` · `losing_trades` | what the unit **closed** |
| `total_long_trades` · `total_short_trades` | the same population split by direction |
| `total_profit` · `total_loss` · `net_profit` | the winners, the losers, and `total_profit − total_loss` |
| `initial_balance` · `current_balance` | what the unit started with, and the **realised** balance at the end |
| `max_equity` | the peak this account's equity reached |
| `total_commission` · `total_swap` · `total_spread_cost` | the unit's cost split |
| `has_error` | the unit produced figures **and** failed |

`has_error` is the one to branch on before rendering anything else as clean. It marks partial
data standing beside an error, so the numbers are real as far as they go and must not be read as
a completed unit. What failed is on [warnings and errors](/api/v1/docs/warnings-errors).

## Two fee totals, and they answer different questions

`total_fees` is the fees of the trades the unit **closed** — the same population every trade row,
every booking period and the ledger sum. `fees_charged` is everything the run charged, the open
positions included. The two differ by exactly the fees of what was still open at the end.

Reading one as the other is how one unit comes to show two fee totals in two places. The split
arrived with contract 18; see [the contract log](/api/v1/docs/contract-log).

## A trade that realised nothing is neither a winner nor a loser

Trades of `+10`, `0` and `−5` are **1 winner and 1 loser**, with `total_trades` 3. A flat trade is
counted among the trades and in neither outcome, so an average loss is not divided by a trade that
lost nothing.

## `win_rate` and `profit_factor` say "undefined" two different ways

`win_rate` is a ratio between 0 and 1 and is never null. A `0.0` beside `total_trades: 0` means
**not measured** — the discriminator is `total_trades` on the same row, and it is always there.

`profit_factor` is null when it is undefined: either the unit had no losing trade, or it never
traded at all. A `0.0` therefore always means measured and zero. The two halves it is the quotient
of are right beside it as `total_profit` and `total_loss`, which is what lets a rate survive being
folded across rows — [run summary](/api/v1/docs/run-summary) carries the same pair under the names
`gross_profit` and `gross_loss`.

See [nulls](/api/v1/docs/nulls) for the three reasons any field in this API is null.

## The drawdown is the account's, and it may be older than the run

`account_max_drawdown` is the whole account from its high to its low, and `account_max_dd_pct` is
the share that decline was of the peak it fell from. A single trade's own excursion is a different
number and lives on [trade history](/api/v1/docs/trade-history).

A live session inherits its predecessor's equity curve when it starts, so the decline may span a
month of restarts — and nothing in the amount itself says so. Three fields say it instead:

| Field | Meaning |
|---|---|
| `drawdown_carried_from` | where the inherited curve came from; **empty** means this unit began its own |
| `drawdown_restarts` | how many restarts the curve has been carried across |
| `drawdown_started_at` | when the curve began — not when it was last handed over |

An empty `drawdown_carried_from` is always the case in a backtest: a scenario starts its own
curve.

## What was still open when the run ended

A run no longer flattens its positions at the end. In a live session that flatten never reached
the venue, and in a backtest it invented an exit the strategy never chose — so a position still
open is reported as what it is.

`net_profit` stays **realised**. The wealth view stands beside it and is never folded in silently:

- `open_positions` — one entry per position still open
- `unrealized_pnl` — what those positions were worth at the last tick; `0.0` when nothing was open
- `final_equity` — the realised balance plus what the open positions are worth
- `final_equity_valued` — whether `final_equity` is a real mark-to-market
- `open_position_count` — on the aggregate row, how many positions the currency's accounts held

**`final_equity_valued: false` is the one to respect.** It means something was still open that no
tick could price, so the figure is the balance alone with the holding counted at zero — the very
understatement this section exists to remove. Do not present it as a valuation.

Each open position carries `position_id`, `direction`, `lots`, `entry_price`, `entry_time`,
`last_price`, `unrealized_pnl` and `valued`. `unrealized_pnl` here is a **mark, not a result**: it
is what the position was worth at the last tick. `valued` says whether there was a tick to value
it at all — a start that aborted before the first one carries the entry price and no valuation
rather than an invented number.

There is deliberately no flag saying a position was inherited at the session's start. That is
answered by the run's cold-start section, keyed by the same `position_id`.

## Who holds the stop, and who holds the target

A position may carry `stop_loss` and `take_profit`, and the level alone does not say who would act
on it. Two fields do, and they are **not the same answer**:

| Value | Meaning |
|---|---|
| `local` | this process watches the tick stream and closes on a breach — a process that dies leaves the position unprotected |
| `venue` | the level rests at the broker and outlives us |
| *(empty)* | no level is set |

`protective_level_enforcement` answers it for the stop, `take_profit_enforcement` for the target.
They differ in ordinary operation: only one order can rest at a venue that offers neither a
one-cancels-other pair nor a bracket, and the stop is the one that rests there, because a bounded
loss is worth more than a captured opportunity. So a position whose stop the venue holds still has
a take profit watched by this process alone. One answer across both levels told an operator the
target survives a restart, and it does not.

## Spot: two balances, not one

A spot account holds an inventory, so one balance cannot describe it. `spot_mode` says which model
the unit ran under, and the spot fields are populated for it:

| Field | Meaning |
|---|---|
| `balances` · `initial_balances` | the holdings per asset, at the end and at the start |
| `base_currency` · `quote_currency` | the symbol's split, taken from the broker's own configuration |
| `spot_est_current` · `spot_est_initial` | the estimated account value now and at the start |
| `spot_est_pnl` · `spot_est_pnl_pct` | the change between them, and the share it was |
| `committed_funds` · `usable_funds` | what unfilled orders still claim, and what is left after them |
| `last_price` | the price the value estimate is marked at |

The currency split comes from the broker's own configuration and never from the symbol string. A
split taken three characters from the end of the symbol is wrong for every instrument whose quote
currency is not three characters long.

`usable_funds` is derived for you. It is the balance after what resting orders have already
claimed, which is the number a reader actually wants and the one most easily computed wrongly.

## What the aggregate row can and cannot say

An aggregate over several units is an aggregate over several **accounts** — a backtest of twenty
scenarios is twenty accounts, a live session is one. `unit_count` says how many are folded into
the row.

That is why the row names its figures the way it does:

- **`final_equity` is null over several accounts.** The one-account figure has nothing to
  describe there. The sum stands beside it as `total_final_equity`, next to the capital it started
  from, `total_initial_balance`.
- **The drawdown trio is the deepest account's**, never a sum, and `account_max_drawdown_unit`
  names which account that was. The amount, the peak it fell from (`max_equity`) and the share
  (`account_max_dd_pct`) all come from that same account — one account's decline shown over
  another's peak was a real defect.
- **Where no account declined**, the trio names the first account and its peak.

Everything that adds up still adds up: trade counts, profit and loss, fees, and `unrealized_pnl`
summed across the currency's units. Nothing is summed across currencies, which is why the list has
one row per currency in the first place.
