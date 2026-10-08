# Pending orders

An order does not become a position the moment the strategy sends it. It waits — in a backtest
for a modelled delay, in a live session for the venue — and while it waits it can be refused, run
out of time, or still be sitting there when the data ends. Trade history shows what came out of
that queue, and nothing shows the queue itself. This section is the queue: how each order left it,
how long the venue took to answer, and which orders were still resting at the end.

**Routes**

```
GET /api/v1/reports/runs/{run_id}/pending-orders
```

**What is not here:** the individual order events. One row is a unit's whole order pipeline, not
an order — [order history](/api/v1/docs/order-history) is where a single order's life is
recorded, and [trade history](/api/v1/docs/trade-history) where the positions it produced are.

## Both kinds of run

A backtest serves one row per scenario, an AutoTrader session one row for the session. Both are
counted by the same rule, from the same record of every order step — the
[order events](/api/v1/docs/order-events) — so a backtest and a session over the same window can be
compared row for row. Where they differ is the waiting itself: a backtest's is the delay it
modelled, a session's is measured at the venue.

## One row is one unit

```json
{ "key": ["name"], "units": [ ... ] }
```

`name` is the unit's name — in a backtest the scenario's — and `symbol` what it traded. The name
is unique by construction: a scenario set that names one unit twice is refused before the run
starts, and an AutoTrader session has one unit. [Row keys](/api/v1/docs/row-keys) also has the
four field names one unit answers to across the sections.

A unit that neither submitted an order nor held one at the end is not listed. An absent unit means
nothing ever waited, not that something went missing.

## The counts

Each submission is counted once, by the venue's first word on it:

| Field | Counts |
|---|---|
| `total_submitted` | orders the unit handed to its venue |
| `total_accepted` | the venue took the order — it rests, or it filled in its answer |
| `total_rejected` | the venue refused it |
| `total_never_confirmed` | the venue never confirmed it — each one is listed below |
| `total_expired` | the end of a backtest's data met the order on its way |

**The four add up to `total_submitted`.** That holds by construction, so a row where it does not
names a step its record is missing — and the run's warnings say so too.

**Accepted is not filled.** An accepted limit or stop order may still rest at its price, expire
when the data ends, or be cancelled; [order history](/api/v1/docs/order-history) is where an
order's ending is recorded.

**An order an earlier session sent is not counted.** A session that takes over orders still working
at the venue when it starts did not submit them; [execution stats](/api/v1/docs/execution-stats)
counts them as adopted.

## Orders never confirmed

`never_confirmed_orders` lists each submission the venue never confirmed, in the order they were
sent. It happens only in a live session — a simulated venue answers every order.

| Field | Meaning |
|---|---|
| `order_id` | the order |
| `submitted_seq` | its submission's `seq` in the [order events](/api/v1/docs/order-events) |
| `event_type` | `undelivered`: the venue confirmed it never received the order · `unaccounted`: the session stopped asking, and the venue may still hold the order |
| `end_reason` | why the asking stopped, where it was given |
| `message` | the sentence that came with it |

An `unaccounted` order is the one to check by hand at the venue.

## How long the venue took to answer

`avg_in_flight_ms`, `min_in_flight_ms` and `max_in_flight_ms` are milliseconds from submission to
the venue's answer — the acceptance or the refusal. In a backtest they are the modelled delay, not
a measurement of a network; in a live session they are measured when the session reads the answer,
which can be up to one heartbeat after it arrived. An acceptance the session learned later by
asking carries no duration: that span would be the asking, not the venue.

`in_flight_count` is how many answers the average rests on. **Weight by it when you average across
units.** Units submit very different numbers of orders, and a mean of means is not a mean.

The three duration figures are null together, and `in_flight_count` is then `0`: no answer in this
unit carried a duration. That is an absence and not a delay of zero — see
[nulls](/api/v1/docs/nulls).

## The orders still resting at the end

`active_limit_orders` and `active_stop_orders` hold the orders that were still resting when the
unit ended — a backtest's data end, a session's last step.

**In a backtest they are not open orders.** The same step that takes this snapshot records every
one of them `expired` in [order history](/api/v1/docs/order-history), so after the run none of
them is still working: resting at data end, then expired. Showing them as live working orders is
the one reading these lists exist to prevent. The snapshot is read after that expiry, from the
lists the expiry deliberately leaves intact, which is why both views exist and agree.

**In a live session some may still be working.** The session cancels its resting orders when it
ends, unless its policy leaves them standing at the venue, and a protective stop over an open
position is always left standing. [Order history](/api/v1/docs/order-history) records which: a
cancelled order ends there, one left standing does not.

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

The counters are derived from the unit's order events when the run writes its report, and stored
with it. This route serves that stored result; nothing is recomputed when you ask for it.

A run that holds no such result answers 404, and the refusal names which of four causes it is:
the run is unknown, it was started without reports, it has produced none yet, or it produced
other sections but not this one. [Errors](/api/v1/docs/errors) has the vocabulary.
