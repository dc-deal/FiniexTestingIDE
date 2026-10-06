# Booking periods

A run's money is booked in periods — a trading day, or the single stretch the run actually
covered. Without them, a thirty-day live session is one number at the end and there is no way to
ask what any single day did, or whether the days add up to the whole. This section is the run's
own ledger: one row per period, each unit's periods folded into its total, and a completeness
check at the bottom.

**Routes**

```
GET /api/v1/reports/runs/{run_id}/booking-periods
GET /api/v1/deployments/{deployment_id}/booking-periods
```

**What is not here:** the arithmetic of a single trade. One row is a period, not a position —
[trade history](/api/v1/docs/trade-history) is where an individual trade lives.

## Two lists, two keys

```json
{ "keys": { "periods": ["unit_name", "period_no"], "unit_totals": ["unit_name"] } }
```

`periods` is one row per unit and period. `unit_totals` folds each unit's periods into one row.
A unit is one account, so every figure on a unit total is defined — its equity included, which no
row spanning several accounts can say. See [row keys](/api/v1/docs/row-keys).

`period_no` is a **per-bot counter that continues across restarts**, not a position in this list.
Two rows of one deployment can both be number 1 when they belong to different bots. Runs recorded
before 2026-09-23 can even repeat a number within one bot: the floor was persisted before the last
period was sealed, so a session restarted a number instead of continuing it.

## One table is one account currency

`currency` says which currency the **totals** are about. `currencies` names every currency the
run booked, the shown one included.

They differ on a multi-currency run, and deliberately: a sum across currencies is not a number.
The rows keep everything, the totals name their one currency. To see another currency's periods,
read the deployment-wide route or the ledger rows behind it.

## What one period says

| Field | Meaning |
|---|---|
| `opened_at` / `closed_at` / `reason` | the period's span and why it ended |
| `trade_count` · `net_pnl` · `total_fees` | what the period's **closed** trades did |
| `commission_cost` + `swap_cost` | equals `total_fees` |
| `spread_cost` | measured, and stands **beside** `total_fees`, never inside it |
| `opening_equity` | what the account stood at when the period opened |
| `final_equity` | what it stood at when the period closed |
| `min_equity` · `max_equity` · `max_drawdown` | the period's **own** band and decline |

`opening_equity` is the previous period's close, or a unit's first observed value. It is not
`final_equity − net_pnl`: that subtraction drops whatever was still open.

The band and the decline are the period's own, not running totals. On this table the question is
what each period did; a cumulative figure would repeat the same number down the column.

`commission_cost`, `swap_cost`, `spread_cost` and `opening_equity` are null on a period recorded
before contract 17 — see [nulls](/api/v1/docs/nulls).

## What a unit total says, and where it differs

The rates are rebuilt from their summed components rather than averaged. The band is the widest
one across the unit's periods.

The decline needs care, because two different figures sit side by side:

- `deepest_period_drawdown` — the deepest decline **within a single period**
- `account_max_drawdown` — the account's own curve decline, cumulative from the unit's start, and
  for a session belonging to a deployment, from the **deployment's** start

A fall that runs across a period boundary is deeper than any one period's own. That is why both
exist: the first is what the table's column shows, the second is what actually happened to the
account. `account_max_dd_pct` is the share that decline was.

## The completeness check, and what it does not prove

```json
{ "total_net_pnl": -4.17, "total_trades": 12,
  "run_net_pnl": -4.17, "run_total_trades": 12, "reconciles": true }
```

`reconciles` compares the periods' sums against the figures the run reports. What it proves is
**completeness**: every closed trade reached exactly one period. A `false` means a trade was lost
between the run and its ledger — dropped in retention, missed by a window, lost in transport.

It does **not** prove the figures are right. The period sum and `run_net_pnl` carry the same
per-trade value along two different routes, so they agree on a wrong number just as readily as on
a right one. Treat `reconciles: true` as "nothing went missing", never as "these numbers are
correct".

**`reconciles` is three-state.** `null` means the run reports no figure in this currency, so the
check did not run — an *absent* check, not a passed one. Rendering it as a tick is the single
reading this field exists to prevent. `run_net_pnl` and `run_total_trades` are null for the same
reason.

A `false` is not an error. It is the finding the table exists to surface, and it is reported
rather than raised.

## `total_final_equity`

The sum of the units' closing equities. A total over separate accounts — no account ever held it,
which is why it does not share a name with `final_equity`. Null where a unit's closing equity was
not recorded.

## Across a deployment

`GET /api/v1/deployments/{deployment_id}/booking-periods` serves every period the deployment
booked across **all** of its sessions — the thirty-day picture in one call, where asking each run
in turn would be a walk.

The rows have the same shape plus `run_id`, which across a deployment is the only thing that tells
two periods apart, and is the hinge into that run's report routes.

Sessions that booked no period are skipped, and counted in `sessions_without_periods` — so an
incomplete history is not read as a quiet one.

**There is no reconciliation line here, and there cannot be one.** Over many runs there is no
single run summary to sum against.

## Where these rows come from

The run-scoped route serves a **stored artifact**, never a figure rebuilt from the ledger on
request. Recomputed from the ledger, the check would be the ledger's sum compared against the
ledger's sum, and it could never fail. A run from before the artifact existed answers 404.
