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

## Which days have a row, and where a day ends

A period a trading day closed ends **at that day's boundary** — `closed_at` is the boundary
instant, never the later moment the run noticed it — and the next period opens there. The tick that
crosses the boundary belongs to the **new** day: its fills, its trades and its move. A live session
and its backtest over the same days therefore book the same periods.

**Every trading day of the market has its row**, whether or not the run heard from it. A day inside
a gap in the data is booked with `tick_count` 0, nothing traded and the valuation it was left at —
say so before you average over rows: a day that observed nothing has no return of its own. A market
that closes books no row for a closed day, and the period after a weekend opens where the last
trading day ended, so a forex row after a weekend reads from the Friday close:

```
opened_at 2026-10-22T21:00Z  closed_at 2026-10-23T21:00Z  anchor   tick_count 61 402
opened_at 2026-10-23T21:00Z  closed_at 2026-10-26T21:00Z  anchor   tick_count 58 977
```

The second row books Monday's trading: the session that opened Sunday evening. Its span holds the
weekend, in which nothing traded.

A session's last period (`reason: session_end`) includes its end instant: a close that fills in the
pass that ends the session is booked in it.

## One table is one account currency

`currency` says which currency the **totals** are about. `currencies` names every currency the
run booked, the shown one included.

They differ on a multi-currency run, and deliberately: a sum across currencies is not a number.
A run's table holds the rows of the shown currency only. A deployment's route holds the rows of
every currency it booked; a backtest's other currencies are not served yet.

## What one period says

| Field | Meaning |
|---|---|
| `opened_at` / `closed_at` / `reason` | the period's span and why it ended |
| `trade_count` · `net_pnl` · `total_fees` | what the period's **closed** trades did |
| `tick_count` | how many ticks the period applied — 0 is a trading day the run never heard from |
| `commission_cost` + `swap_cost` | equals `total_fees` |
| `spread_cost` | measured, and stands **beside** `total_fees`, never inside it |
| `modelled_spread_cost` | crossed against quotes a backtest modelled for data without one — beside `spread_cost`, the two adding up |
| `opening_equity` | what the account stood at when the period opened |
| `final_equity` | what it stood at when the period closed |
| `min_equity` · `max_equity` · `max_drawdown` | the period's **own** band and decline, in the account's currency — the decline is an amount, and no share of it is served per period |
| `account_max_drawdown` · `account_max_dd_pct` | what the period **carries**: the account's decline so far, as an amount and as its share, since the account's equity curve began — a backtest's start, or for an AutoTrader session the curve it continues from the session before wherever its profile carries the risk history over, whatever its deployment |
| `unrealized_pnl` · `open_position_count` | what was still open when the period was sealed |

`opening_equity` is the previous period's close, or a unit's first observed value. It is not
`final_equity − net_pnl`: that subtraction drops whatever was still open.

**A period has two kinds of figure: what it did and what it carries.** The band and the decline in
`min_equity`, `max_equity` and `max_drawdown` are the period's own. `account_max_drawdown` is the
account's decline up to the period's seal, so it repeats down the column until a deeper fall moves
it — on a day that fell 40.00 at most, the account can stand 6.8 % below its peak:

```
period 3 · net_pnl +4.10 · max_drawdown 40.00     — what the day did
           account_max_dd_pct 6.8 % · 1 open, unrealized_pnl −2.30   — what it carried
```

The four carried fields are null on a period whose record does not hold them. A run recorded
before contract 35 stored its report without them, so its own route serves them null, while its
deployment's route, which reads the ledger, serves what the ledger recorded for the same period.

`commission_cost`, `swap_cost`, `spread_cost` and `opening_equity` are null on a period recorded
before contract 17, `modelled_spread_cost` on one recorded before contract 33, and `tick_count` on
one recorded before contract 40 — see [nulls](/api/v1/docs/nulls).

`final_equity`, `min_equity`, `max_equity` and `max_drawdown` are null on a period that could value
the account at no point: a spot session whose first period was sealed before its first tick priced
its holdings. The band otherwise starts at the value the period opened with, so a fall between the
boundary and the first tick of the day is that day's decline.

## What a unit total says, and where it differs

The rates are rebuilt from their summed components rather than averaged. The band is the widest
one across the unit's periods.

The decline needs care, because two different figures sit side by side:

- `deepest_period_drawdown` — the deepest decline **within a single period**
- `account_max_drawdown` — the account's own curve decline, cumulative since its equity curve
  began: a backtest's start, or for an AutoTrader session the curve it continues from the session
  before wherever its profile carries the risk history over — the deployment does not decide it

A fall that runs across a period boundary is deeper than any one period's own. That is why both
exist: the first is what the table's column shows, the second is what actually happened to the
account. `account_max_dd_pct` is the share that decline was. Both are null where none of the
unit's periods measured one.

## The completeness check, and what it does not prove

```json
{ "total_net_pnl": -4.17, "total_trades": 12,
  "run_net_pnl": -4.17, "run_total_trades": 12, "reconciles": true }
```

`total_net_pnl`, `total_fees` and `total_trades` are the sums over the periods — the table's own
totals, and the figures to render beside it. `run_net_pnl` and `run_total_trades` are what the run
itself reports for the same currency; they are there to be compared against, and when the check
holds they carry the same value.

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

**A session books its days while it runs**: each trading day's row appears when the day closes, so
this route shows the days a running session has sealed so far, and those of a session that died
before its end. A session's last row with `reason: session_end` is what says it ended; without one
it is still running or did not end cleanly.

**There is no reconciliation line here, and there cannot be one.** Over many runs there is no
single run summary to sum against.

## Where these rows come from

The run-scoped route serves a **stored artifact**, never a figure rebuilt from the ledger on
request. Recomputed from the ledger, the check would be the ledger's sum compared against the
ledger's sum, and it could never fail. A run from before the artifact existed answers 404.
