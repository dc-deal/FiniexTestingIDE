# Aggregated portfolio

The headline roll-up says what a currency's accounts earned. It does not say what that cost, how
the orders fared, how long a resting order waited, or — in a spot account — what the account
actually still holds. Derived per consumer from the unit rows, those answers come out slightly
differently every time: one averages a rate that cannot be averaged, another pairs one account's
decline with another's peak. This section is the rich per-currency view, derived once and served
as figures rather than as ingredients.

**Routes**

```
GET /api/v1/reports/runs/{run_id}/aggregated-portfolio
```

**What is not here:** the per-unit rows and the lean per-currency roll-up
([portfolio](/api/v1/docs/portfolio)), the cross-section KPIs a sweep ranks on
([run summary](/api/v1/docs/run-summary)), and the per-unit order and lifecycle detail these
totals are folded from ([execution stats](/api/v1/docs/execution-stats),
[pending orders](/api/v1/docs/pending-orders)).

## Which runs have it

This is a backtest's view. Multi-currency aggregation is a simulation concern — a live session is
one account in one currency, and its roll-up is the lean aggregate on
[portfolio](/api/v1/docs/portfolio).

A run whose pipeline does not write this section answers `404 artifact_not_produced`. That is not
an error on your side: the two pipelines produce different sets of sections, so read `artifacts`
on the run index and ask only for what is there. See [errors](/api/v1/docs/errors) and
[runs](/api/v1/docs/runs).

## One entry per account currency

```json
{ "currencies": [ { "currency": "USD", "scenario_count": 4, "scenario_names": [ ... ],
                    "is_spot": false, "is_mixed": false, "combined": { ... } } ] }
```

Nothing is summed across currencies, because a sum across currencies is not a number.

`scenario_names` lists the units folded into the entry, under the same name they carry everywhere
else in this API — see [row keys](/api/v1/docs/row-keys). `scenario_count` is how many there were.

## Three figure blocks, and when the second and third exist

| Field | Holds |
|---|---|
| `combined` | always — every unit of the currency, margin and spot together |
| `margin` | only when `is_mixed` — the currency's margin accounts alone |
| `spot` | only when `is_mixed` — the currency's spot accounts alone |

`is_mixed` means the currency holds both kinds of account, which is the one case where a single
block would describe two different account models at once. For a currency that is purely one or
the other, only `combined` is filled and the two sub-blocks are absent.

`is_spot` on the entry says the currency is purely spot. On a block, `is_spot` says the same of
that block, and `label` tags a sub-block as `Margin` or `Spot` — empty where there is nothing to
tell apart.

All three blocks have the same shape, so a renderer written for one renders all of them.

## `headline` is the row the portfolio aggregate serves

Every block opens with `headline`, which has the shape of the per-currency row
[portfolio](/api/v1/docs/portfolio) serves — trade counts, win rate, profit factor, profit, loss,
net profit, the two fee totals, the drawdown trio, `final_equity` and the sums beside it. Its
rules hold here unchanged, including the ones that matter most: `final_equity` is null over
several accounts, and the drawdown trio belongs to the deepest single account.

What differs is the population. On `combined` it is every unit of the currency, which is how the
portfolio aggregate groups too; on `margin` or `spot` it is only that account model's units.

Everything outside `headline` is the detail that row deliberately does not carry.

## Two peaks, and they are not the same peak

This is the field pair most easily read wrong, because a single name once served both:

- `headline.max_equity` — the peak the **drawdown** fell from, on the account the drawdown
  belongs to
- `highest_equity` — the highest peak **any** account of this currency reached, with
  `highest_equity_scenario` naming it

They are different accounts whenever the deepest decline did not happen to the account that climbed
highest. Showing one scenario's decline beside another's peak is the defect the split exists to
prevent.

The account that declined is named twice, under two field names, and the difference is worth
knowing: `account_max_drawdown_scenario` on the block, `account_max_drawdown_unit` inside
`headline`. Both name the same unit.

## The risk figures

`account_max_dd_pct` is the share the decline was of the peak it fell from.

`recovery_factor` is **null over several accounts**, and that null is correct rather than missing:
their summed profit over one account's drawdown is a quotient of two different populations, so no
honest number exists. It is one of the figures that became null when every figure started saying
which account it is about — see [the contract log](/api/v1/docs/contract-log), and
[nulls](/api/v1/docs/nulls) for how to render an absence.

## Balances, and the P&L that comes from them

| Field | Meaning |
|---|---|
| `initial_balance` · `final_balance` | the currency's summed opening and closing balances |
| `balance_pnl` | `final_balance − initial_balance` |
| `balance_pnl_pct` | the share that change was |

These are balance movements, not the realised trade result — `headline.net_profit` is that, and
what the block still held at the end is `headline.unrealized_pnl`. Read them together rather than
as alternatives.

## What the trading cost

`total_commission`, `total_swap` and `total_spread_cost` are the cost split summed across the
block's units; `avg_spread` stands with them. How a single period's split relates to its fee total
is stated on [booking periods](/api/v1/docs/booking-periods).

`avg_win` and `avg_loss` are the mean winning and losing trade, and `total_long_trades` /
`total_short_trades` split the block's trades by direction.

## Execution and resting orders, per currency

| Field | Meaning |
|---|---|
| `orders_submitted` · `orders_executed` · `orders_rejected` | what the block's units asked for and got |
| `sl_tp_triggered` | closes that came from a stop or a target |
| `execution_rate_pct` | executed over submitted and adopted |
| `pending_total_submitted` | orders the block's units handed to their venues |
| `pending_total_accepted` · `pending_total_rejected` · `pending_total_never_confirmed` · `pending_total_expired` | the venue's first word on each — they add up to the submitted |
| `pending_avg_in_flight_ms` · `pending_min_in_flight_ms` · `pending_max_in_flight_ms` | how long the venue took to answer |
| `pending_active_limit_count` · `pending_active_stop_count` | what was still resting when the run ended |

The three duration figures are **null when nothing measured one**, never zero — a zero-millisecond
answer is a claim, and an unmeasured one is not. [Nulls](/api/v1/docs/nulls) has the general
rule. The per-unit lifecycle these totals fold is on
[pending orders](/api/v1/docs/pending-orders), and the per-unit order counts are on
[execution stats](/api/v1/docs/execution-stats).

## Spot: an inventory needs two numbers

A spot account holds an asset as well as cash, so one balance understates it. `spot_scenarios`
carries one row per spot unit:

| Field | Meaning |
|---|---|
| `scenario_name` | the unit, under the name it carries everywhere else |
| `base_currency` · `quote_currency` | the symbol's split, taken from the broker's own configuration |
| `quote_balance` · `base_balance` | what it held at the end |
| `quote_initial` · `base_initial` | what it held at the start |
| `last_price` | the price the estimate is marked at |
| `est_current` | `quote_balance + base_balance × last_price` — **0 where there is no base holding** |
| `est_initial` | the starting holdings' estimated value |
| `has_base_holdings` | whether there was a base holding to value |

`spot_total_est_current` and `spot_total_est_initial` are those estimates summed over the block,
and `spot_has_base_holdings` says whether any unit in it held the base asset at all.

**Read `has_base_holdings` before reading `est_current` as zero.** A zero estimate on an account
with no base holding means no estimate was formed, not that the account is worth nothing — its
cash is `quote_balance`.

The split comes from the broker's configuration and never from the symbol string, which is wrong
for every instrument whose quote currency is not three characters long. The initial estimate used
to drop a starting base holding whenever the account ended without one. Both corrections arrived
together — see [the contract log](/api/v1/docs/contract-log).
