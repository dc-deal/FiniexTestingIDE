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

`units` declares no key at all, which is not the same as declaring an empty one. The unit name
separates the rows in practice, but the response does not promise it yet.

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
counts beside it. Read it alongside them, never added into them.

**None of them is ever null on this route.** They are whole numbers, and a zero is a real zero
rather than something nobody measured. The same names do answer `null` on a sweep combination
folded from booking periods, which carry no order counts — see [sweeps](/api/v1/docs/sweeps) and
[nulls](/api/v1/docs/nulls) for why the two differ.

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

## Where these rows come from

The counts are kept by the run while it goes and written once when it closes. This route serves
that stored result; nothing is recomputed when you ask for it.

A run that holds no such result answers 404, and the refusal names which of four causes it is:
the run is unknown, it was started without reports, it has produced none yet, or it produced
other sections but not this one. [Errors](/api/v1/docs/errors) has the vocabulary.
