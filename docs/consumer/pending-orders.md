# Pending orders

An order does not become a position the moment the strategy sends it. It waits — in a backtest
for a modelled delay, in a live session for the venue — and while it waits it can be refused, run
out of time, or still be sitting there when the data ends. Trade history shows what came out of
that queue, and nothing shows the queue itself. This section is the queue: how many orders left
it and by which exit, how long they waited, and which were still resting at the end.

**Routes**

```
GET /api/v1/reports/runs/{run_id}/pending-orders
```

**What is not here:** the individual order events. One row is a unit's whole order pipeline, not
an order — [order history](/api/v1/docs/order-history) is where a single order's life is
recorded, and [trade history](/api/v1/docs/trade-history) where the positions it produced are.

## Every row served here is a backtest row

**An AutoTrader session serves no unit at all**: a live session's result carries no pending
counters into this report. The section still answers — `units` is an empty list, which is the
expected answer for a live run and not a missing artifact. [Run kinds](/api/v1/docs/run-kinds)
is how you tell which kind of run you are holding.

Everything below therefore describes a backtest, and its latencies are modelled rather than
observed at a venue.

## One row is one unit

```json
{ "key": ["name"], "units": [ ... ] }
```

`name` is the unit's name — in a backtest the scenario's — and `symbol` what it traded. The name
is unique by construction: a scenario set that names one unit twice is refused before the run
starts, and an AutoTrader session has one unit. [Row keys](/api/v1/docs/row-keys) also has the
four field names one unit answers to across the sections.

A unit that neither resolved a pending order nor held one at the end is not listed. An absent
unit means nothing ever waited, not that something went missing.

## The counts, and what `total_filled` does not mean

| Field | Counts |
|---|---|
| `total_resolved` | orders that left the queue, by any exit |
| `total_filled` | orders that arrived after their delay |
| `total_rejected` | orders refused once the waiting was over |
| `total_timed_out` | orders the broker did not answer within their timeout |
| `total_force_closed` | orders still in the queue when the data ended, resolved by force |

**`total_filled` does not mean filled.** In a backtest it counts each order that *arrived* after
its delay — so a limit or a stop order that only began resting at its price is counted here
whether it later filled, expired when the data ended, or was cancelled by the strategy. Arrival
and fill are one number in this section; nothing served here separates them.

**`total_force_closed` and the active lists below are different orders.** A force-closed order
was still waiting out its delay when the data ended, so it never arrived. An order in the active
lists did arrive, and was resting at its price.

**`total_timed_out` belongs to a live session**, where a broker can fail to answer. Since a live
session serves no row here, it reads `0` on everything this route answers with.

The counts add up, and that was checked rather than assumed. Measured 2026-10-05 over every
stored unit: `total_resolved` is the unit's `open` orders on `pending` status in
[order history](/api/v1/docs/order-history) plus the closes the strategy sent itself, and
`total_filled` is `total_resolved` minus the rejected, timed-out and force-closed counts.

## How long they waited

`avg_latency_ms`, `min_latency_ms` and `max_latency_ms` are milliseconds. In a backtest they are
the modelled delay from submission to arrival — not a measurement of a network.

`latency_count` is how many samples the average rests on. **Weight by it when you average across
units.** Units resolve very different numbers of orders, and a mean of means is not a mean.

The three latency figures are null together, and `latency_count` is then `0`: no order in this
unit produced a sample. That is an absence and not a delay of zero — see
[nulls](/api/v1/docs/nulls).

## The orders still resting at the end

`active_limit_orders` and `active_stop_orders` hold the orders that were still resting when the
unit's data ended.

**They are not open orders.** In a backtest the same step that takes this snapshot records every
one of them `expired` in [order history](/api/v1/docs/order-history), so after the run none of
them is still working: resting at data end, then expired. Showing them as live working orders is
the one reading these lists exist to prevent. The snapshot is read after that expiry, from the
lists the expiry deliberately leaves intact, which is why both views exist and agree.

Which list an order sits in follows from its type, not from its fate. A `limit` order is in the
limit list; a `stop` or `stop_limit` whose trigger was never reached is in the stop list. A
`stop_limit` whose stop did trigger has become a limit order.

| Field | Meaning |
|---|---|
| `order_id` | the id this order carries in order history too, where it names the position |
| `order_type` | `limit` · `stop` · `stop_limit` |
| `direction` | `long` · `short` |
| `lots` | the order's size |
| `entry_price` | the limit price on a `limit`; the trigger price on a `stop` or `stop_limit` |
| `limit_price` | the fill limit of a `stop_limit`, and null on every other type |
| `stop_loss` / `take_profit` | the levels the order carries, null where it carries none |

`entry_price` is one field with two meanings, so read it together with `order_type`.

**A resting order is not necessarily an entry.** A protective stop belonging to an open position
rests in exactly the same way and appears in the stop list; in order history its expiry is
recorded on the close side.

The two lists declare no key at all, which is not the same as declaring an empty one: nothing in
the response says yet what makes one of their rows unique. [Row keys](/api/v1/docs/row-keys) has
the difference.

## Where these rows come from

The counters are kept by the run while it goes and written once when it closes. This route serves
that stored result; nothing is recomputed when you ask for it.

A run that holds no such result answers 404, and the refusal names which of four causes it is:
the run is unknown, it was started without reports, it has produced none yet, or it produced
other sections but not this one. [Errors](/api/v1/docs/errors) has the vocabulary.
