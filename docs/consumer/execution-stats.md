# Execution stats

Trade history says what a run traded. It cannot say what it *tried* to trade: an order that was
refused leaves no trade behind, so a run that sent ten orders and had six refused looks exactly
like a run that sent four. This section counts the orders — one count for each way an order can
start or end — one row per unit, and the run's summed total beside them.

**Routes**

```
GET /api/v1/reports/runs/{run_id}/execution-stats
```

**What is not here:** the orders themselves. These are counts, not records. A single order, the
reason it was refused and the time it happened are in
[order history](/api/v1/docs/order-history); what the orders earned and cost is in
[portfolio](/api/v1/docs/portfolio); how long an order waited before it resolved is in
[pending orders](/api/v1/docs/pending-orders).

## One row is one unit

`units` carries one row per run unit — in a backtest a scenario, in a live session the session
itself. Both kinds of run are served here. A unit is one account, which is why these rows are
never folded together; see [run kinds](/api/v1/docs/run-kinds).

`name` is the unit's name and `symbol` the instrument it traded. The name is the same value the
other per-unit sections carry under their own field names, and joining on it is meant rather than
incidental — [row keys](/api/v1/docs/row-keys) has the four spellings.

`keys.units` is `["name"]`: the unit's name is what makes a row unique, and the answer declares it
— see [row keys](/api/v1/docs/row-keys).

Only units that recorded order counts appear. The complete list of units a run declared, the ones
that produced nothing included, is the roster in
[scenario details](/api/v1/docs/scenario-details).

## The counts

Each count carries the name of what it counts. Two count how an order STARTED — handed to the
venue, or taken over from a previous session; every other `orders_<status>` counts the orders
whose ending in the [order history](/api/v1/docs/order-history) has that status.

| Field | Counts |
|---|---|
| `orders_submitted` | orders handed to the venue — opens, closes and protective orders |
| `orders_adopted` | orders a previous session sent and this one took over at its start — a live session only; a backtest starts with none |
| `orders_executed` | orders that executed — closing orders as well as opening ones |
| `orders_denied` | orders refused before anything was sent — lot size, funds, the run's own pre-trade checks |
| `orders_rejected` | orders the venue refused |
| `orders_cancelled` | orders cancelled — on request, or by the venue |
| `orders_expired` | orders that ran out of time — the end of a backtest's data, or the venue's expiry |
| `orders_undelivered` | orders the venue confirmed it never received |
| `orders_unaccounted` | orders the session stopped asking about; the venue may still hold them |
| `orders_failed` | the orders that ended as a failure — denied, rejected, undelivered and unaccounted together |
| `sl_tp_triggered` | closes triggered by a stop-loss or a take-profit level |

**A refusal made here is not one the venue made.** An order stopped by the run's own pre-trade
checks never reaches the venue: it is `orders_denied`, and it is not counted as submitted.
`orders_rejected` is the venue's answer alone.

**A close is an order like an open**, so `orders_executed` counts both and a round trip executes
two orders.

**`orders_failed` is counted where each ending is booked, not from the order history.** That
history may hold fewer rows than a long run produced; this count holds them all.

**`sl_tp_triggered` counts closes, not submissions.** It is a different population from the
counts beside it. Read it alongside them, never added into them. A close counts whether the level
was one the position carried or one the strategy kept itself and closed at — on a venue that holds
no level for a position, the second is the only kind there is.

**None of them is ever null on this route.** They are whole numbers, and a zero is a real zero
rather than something nobody measured. The same names on a sweep combination are the sum of its
booking periods' own counts, which add up to these; they answer `null` there only for a run
recorded before contract 42, when a period carried none — see [sweeps](/api/v1/docs/sweeps) and
[nulls](/api/v1/docs/nulls).

## `totals` is one object, not one per currency

```json
{ "totals": { "orders_submitted": 42, "orders_adopted": 0, "orders_executed": 38,
              "orders_denied": 1, "orders_rejected": 3, "orders_cancelled": 1, "orders_expired": 0,
              "orders_undelivered": 0, "orders_unaccounted": 0, "orders_failed": 4,
              "sl_tp_triggered": 6 } }
```

Counts are currency-agnostic — an order is an order whatever currency the account holds — so the
total is a plain sum of the rows and there is exactly one of it. This is deliberately unlike the
[portfolio](/api/v1/docs/portfolio) roll-up, which splits by account currency, because money in
EUR and money in USD is not one scale and a sum across them is not a number.

`totals` is present even when `units` is empty. Its fields are then zero.

## How far the fills landed — `fill_slippage`, `fill_slippage_totals`

Every fill is measured against a price set before it could move it — a market order against the mid
when it was sent, a limit against its limit, a stop against its trigger, a stop-loss or take-profit
close against the level that fired; each fill's own figure is on the
[order history](/api/v1/docs/order-history). These two lists summarise them, one row per reference:

| Field | Meaning |
|---|---|
| `measured_against` | `submission_mid` · `limit_price` · `trigger_price` · `protective_level` |
| `fills` | the fills measured against it |
| `fills_measured` | of those, the ones whose reference price was captured |
| `adverse_pct_mean` | their mean, in percent of each fill's reference — positive is a cost |
| `adverse_pct_max` | the costliest of them |

`fill_slippage` has a row per unit and reference (it also carries the unit's `name`);
`fill_slippage_totals` has a row per reference over every unit's fills, computed from the fills
themselves — never an average of the units' averages. The answer declares each list's row key in
`keys`; see [row keys](/api/v1/docs/row-keys).

**Per fill and in percent, so symbols compare; never market and limit fills together.** A limit
fill can only meet or beat its limit, so averaged in with market fills it would make the market's
cost look smaller than it was. The mean and the maximum are null where no fill of that reference was
measured. Every fill counts, also the fills the order history's ceiling has dropped, and the mean is
unweighted: a fill of a small order counts as much as one of a large order, and an order filled in
three parts counts three times. A run recorded before these lists existed serves both empty, the
same as a run without a fill.

```json
{ "fill_slippage_totals": [
    {"measured_against": "submission_mid", "fills": 212, "fills_measured": 212,
     "adverse_pct_mean": 0.0041, "adverse_pct_max": 0.0612},
    {"measured_against": "protective_level", "fills": 9, "fills_measured": 9,
     "adverse_pct_mean": 0.0187, "adverse_pct_max": 0.0730}] }
```

## Where these rows come from

The counts are kept by the run while it goes and written once when it closes. This route serves
that stored result; nothing is recomputed when you ask for it.

A run that holds no such result answers 404, and the refusal names which of four causes it is:
the run is unknown, it was started without reports, it has produced none yet, or it produced
other sections but not this one. [Errors](/api/v1/docs/errors) has the vocabulary.
