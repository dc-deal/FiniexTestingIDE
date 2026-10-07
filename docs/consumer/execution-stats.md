# Execution stats

Trade history says what a run traded. It cannot say what it *tried* to trade: an order that was
refused leaves no trade behind, so a run that sent ten orders and had six refused looks exactly
like a run that sent four. This section counts the orders — sent, executed, refused — one row
per unit, and the run's summed total beside them.

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

`units` declares no key at all, which is not the same as declaring an empty one. The unit name
separates the rows in practice, but the response does not promise it yet.

Only units that recorded order counts appear. The complete list of units a run declared, the ones
that produced nothing included, is the roster in
[scenario details](/api/v1/docs/scenario-details).

## The four counts

| Field | Counts |
|---|---|
| `orders_submitted` | orders handed to the venue |
| `orders_executed` | orders that executed |
| `orders_rejected` | orders refused — margin, validation, and the rest |
| `sl_tp_triggered` | closes triggered by a stop-loss or a take-profit level |

**A refusal counts here whether the venue refused it or this side did.** An order stopped by the
run's own pre-trade checks never reaches the venue, and it is counted beside the ones the venue
turned down — so the count is the whole of what was refused, not only the half that travelled.

**`sl_tp_triggered` counts closes, not submissions.** It is a different population from the three
counts beside it. Read it alongside them, never added into them.

**None of the four is ever null on this route.** They are whole numbers, and a zero is a real
zero rather than something nobody measured. The same four names do answer `null` on a sweep
combination folded from booking periods, which carry no order counts — see
[sweeps](/api/v1/docs/sweeps) and [nulls](/api/v1/docs/nulls) for why the two differ.

## `totals` is one object, not one per currency

```json
{ "totals": { "orders_submitted": 42, "orders_executed": 38, "orders_rejected": 4,
              "sl_tp_triggered": 6 } }
```

Counts are currency-agnostic — an order is an order whatever currency the account holds — so the
total is a plain sum of the rows and there is exactly one of it. This is deliberately unlike the
[portfolio](/api/v1/docs/portfolio) roll-up, which splits by account currency, because money in
EUR and money in USD is not one scale and a sum across them is not a number.

`totals` is present even when `units` is empty. Its fields are then zero.

## Where these rows come from

The counts are kept by the run while it goes and written once when it closes. This route serves
that stored result; nothing is recomputed when you ask for it.

A run that holds no such result answers 404, and the refusal names which of four causes it is:
the run is unknown, it was started without reports, it has produced none yet, or it produced
other sections but not this one. [Errors](/api/v1/docs/errors) has the vocabulary.
