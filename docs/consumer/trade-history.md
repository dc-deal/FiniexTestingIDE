# Trade history

A run's result is one number, and one number cannot be questioned: it does not say which trade
earned it, what the trade cost to hold, or how far the price ran against the position before it
came back. This section is the run's blotter — one row per **closed** trade, with both ends of it,
its costs itemised, its worst and best excursion while it was open, and the individual fills
behind the entry and the exit.

**Routes**

```
GET /api/v1/reports/runs/{run_id}/trade-history
```

**What is not here:** a position that was still open when the run ended — that is in
[portfolio](/api/v1/docs/portfolio). What an order did before it became a trade, and every order
that was refused, is in [order history](/api/v1/docs/order-history). Money grouped by day rather
than by trade is in [booking periods](/api/v1/docs/booking-periods).

## Three lists, three keys

```json
{ "keys": { "trades":          ["scenario_name", "position_id", "exit_tick_index"],
            "analytics":       ["currency"],
            "scenario_totals": ["scenario_name", "currency"] } }
```

`trades` is the blotter. `analytics` is one entry per account currency. `scenario_totals` is the
per-unit footer, so you never have to re-sum the rows.

A trade is **not** a position: a partial close books several records of one position, so
`position_id` alone repeats. The key needs the unit, the position and the exit — see
[row keys](/api/v1/docs/row-keys), which also carries the measurement behind that.

## What one row is

One closed trade, within one run unit. `scenario_name` is that unit — a scenario in a backtest,
the session in a live run — and it is the join into every other section.

| Field | Meaning |
|---|---|
| `position_id` · `symbol` · `direction` · `lots` | which position, which instrument, `long` or `short`, and this row's size |
| `entry_price` · `entry_time` | where and when it was opened, the time ISO-8601 UTC |
| `exit_price` · `exit_time` | where and when it was closed |
| `duration_s` | how long it was open, in seconds |
| `entry_type` | how the position was opened — `market`, `limit`, `stop`, `stop_limit` |
| `entry_side` · `exit_side` | `buy` or `sell` — what each end actually did |
| `entry_tick_index` · `exit_tick_index` | the chronological sort key within the unit |
| `stop_loss` · `take_profit` | the levels that were set, null where none was |
| `currency` | the account currency every money figure on the row is in |

## The money on a row, and the figure that stands outside it

`gross_pnl` is the result **before** fees and `net_pnl` the result after them. `total_fees` is what
was charged, and `commission_cost` and `swap_cost` are its two parts. `swap_cost` is signed: a
positive value is a debit, a negative one a credit.

`spread_cost` is the one most likely to be counted twice. It is **measured, not charged**: it is
the effective spread the fills crossed, and it is already inside `gross_pnl`. So it is reported
**beside** `total_fees` and never inside it. It is signed like the swap, and a negative value is a
price improvement — what a resting limit order earns by filling inside the spread.

## How far it went while it was open

The excursion fields are the worst and the best the position reached between its two ends, which
the two prices alone cannot show — a trade that closed flat may have been deeply under water
first.

`mae_price` and `mfe_price` are the most adverse and most favourable prices reached. `mae_pnl` and
`mfe_pnl` are the gross P&L at those moments. `mae_distance` and `mfe_distance` express the same
two distances in the instrument's own price unit, and `price_unit` labels which that is — `pip` on
forex, `tick` on crypto.

`r_multiple` is the result in units of what the trade risked: `net_pnl` over the loss the stop
would have taken. It is **null when the trade had no stop loss** — there is nothing to divide by,
so R is undefined rather than zero. See [nulls](/api/v1/docs/nulls).

## Slippage, and when it was not measurable

`entry_slippage` and `exit_slippage` compare the fill against the mid price at the moment of
submission, signed so that **a value above zero means worse than the submission mid**.
`entry_slippage_pct` and `exit_slippage_pct` are the same comparison as a share.

All four are null when no submission tick was captured — a close performed in cleanup has no
submission moment to measure against, and neither does a record old enough to predate the
measurement.

## The fills behind a row

`entry_executions` and `exit_executions` carry the individual executions that made up each end.
One execution is:

| Field | Meaning |
|---|---|
| `trade_id` | the venue's identifier for this one execution |
| `side` | `buy` or `sell` |
| `volume` · `price` | the size and price of **this fill** |
| `fee` · `fee_currency` | what this fill was charged, and in which currency |
| `liquidity` | `maker` or `taker` |
| `timestamp` | ISO-8601 UTC, and the **empty string** where it is absent |
| `shared_by` | how many rows of this unit carry this same fill |

`liquidity` is `maker` only where the order genuinely rested at the venue and provided liquidity.
A limit order whose price was already past its level crosses the book the moment it arrives and is
charged the taker rate — measured against the venue on 2026-09-08. So a `limit` entry type and a
`taker` fill are not a contradiction.

The `timestamp` is the one field in this section where absence is an empty string rather than
null. Treat the empty string as absent and never as a value.

### Never sum `volume` across rows

`shared_by` says how many trade rows of the same unit carry this execution; `1` means this row
alone. A partial close copies the position's entry fills onto **every** record it produces, so one
fill legitimately appears on several rows. `volume` is the fill's own size — the row's share of it
is the row's `lots`.

It is counted over the whole unit when the report is built, which is why it still holds on a
filtered list: hiding the other rows does not change the number of rows that carry the fill. It is
null on a report written before contract 17 — see [nulls](/api/v1/docs/nulls) and the
[contract log](/api/v1/docs/contract-log).

## `close_reason`

`sl_triggered` or `tp_triggered` where a protective level fired. The **empty string** where the
position was closed through the ordinary close path — in a backtest that is always the strategy's
own close. The empty string is a known rough edge and is to be replaced by a named value.

A fourth value, `scenario_end`, exists and no run produces it today. It used to mark an end-of-run
force close, which was removed because in a live session it never reached the venue and in a
backtest it invented an exit the strategy never chose.

## The filters, and what follows them

Four query parameters narrow the list: `symbol`, `close_reason`, `start` and `end`.

**`start` and `end` bound `entry_time`, inclusively at both ends.** A trade opened on Tuesday and
closed on Wednesday falls in Tuesday's window — the filter asks what you *opened*, not what was
realised. The realisation view is [booking periods](/api/v1/docs/booking-periods), where a trade
belongs to the period it paid out in. A timestamp that is not ISO-8601 is refused rather than
ignored; see [errors](/api/v1/docs/errors).

`count`, `symbols`, `analytics` and `scenario_totals` are all rebuilt over the rows that survive
the filter. So `count` is the number of rows served, never the number of trades the run closed,
and the analytics describe your filtered selection rather than the run. `shared_by` is the
deliberate exception, for the reason above.

## The analytics, one entry per account currency

Never mixed across currencies, because a P&L summed over two currencies is not a number. Each
entry names its `currency` and the `trade_count` it covers.

| Field | Meaning |
|---|---|
| `expectancy` | mean R over the trades that have a defined R |
| `r_trade_count` | trades that had a stop loss, so R is defined |
| `avg_win_r` · `avg_loss_r` | mean R of winners and of losers |
| `r_win_count` · `r_loss_count` | how many of the R-defined trades won and lost |
| `avg_mae_winners` · `avg_mae_losers` | mean adverse excursion, as P&L |
| `avg_mfe_losers` | mean favourable excursion on losers — the "left on the table" read |
| `largest_mae` · `largest_mfe` | the single worst and best excursion, as a P&L magnitude |
| `avg_trade_duration_s` | mean holding time in seconds |
| `gross_pnl` · `net_pnl` · `total_fees` | sums over the group |
| `max_consecutive_wins` · `max_consecutive_losses` | the longest unbroken run, in realisation order |

`avg_win_r` and `avg_loss_r` are **null when not measured, never `0.0`** — a measured zero and an
absent measurement must not look alike. Branch on `r_win_count` and `r_loss_count` rather than on
`r_trade_count`: a run can have R-defined trades and still no winner among them.

A large `avg_mae_winners` against the size of the wins says the stop sat too tight. `largest_mae`
is the figure a stop level is actually judged against — a mean says how much heat the average
trade took, this says how much the worst one did.

**`max_consecutive_wins` and `max_consecutive_losses` do not combine.** A streak can cross a
boundary, so taking the larger of two groups understates the truth: runs of 2 and 3 in adjacent
periods can be one run of 5. Do not aggregate these two across currencies, units or periods.

## Per-scenario totals

`scenario_totals` is one row per unit and currency: `trade_count`, `gross_pnl`, `net_pnl`,
`total_fees` and `total_swap`, the signed swap summed over the unit. It is the footer line, served
so that no consumer has to re-sum the rows and arrive at a different number.

## Retention: this list has a ceiling

A run keeps its closed trades in a buffer whose size the server that produced the run sets, and
which that server can also switch off. Where it is set and a run closes more trades than it holds,
the oldest are discarded and the run logs a warning the first time it happens —
[warnings and errors](/api/v1/docs/warnings-errors) is where a run's warnings are served.

The run's own trade counts are kept on counters beside this list, not derived from it, so they do
not shrink with it. If `count` here is lower than the trade count in
[portfolio](/api/v1/docs/portfolio) or [run summary](/api/v1/docs/run-summary), and no filter is
set, the buffer is the reason.

## When there is no report

A run with no trade-history artifact answers 404, and the refusal names which of four causes it
was: the run is unknown, it was started without reports, it has produced none yet, or it produced
other sections and not this one. See [errors](/api/v1/docs/errors) and
[run kinds](/api/v1/docs/run-kinds) for what a run was commissioned to write.
