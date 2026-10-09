# Order events

[Order history](/api/v1/docs/order-history) keeps a row for each submission and one for every way an
order ENDED. What happened in between is missing there: when the venue took the order, when a stop
triggered, every cancel and modification that was asked for and how it was answered, an answer that
never came and the asking that settled it. This stream keeps all of it — one line per step in an
order's life, in the order the steps happened. A live session's stream also holds what the venue
reported each time the session asked it, in a second list on the same counter.

**Routes**

```
GET /api/v1/reports/runs/{run_id}/order-events
GET /api/v1/reports/runs/{run_id}/order-events?scenario_name=<unit>&order_id=<order>
```

**What is not here:** what a closed position earned — that is
[trade history](/api/v1/docs/trade-history). The per-unit counts of orders submitted, executed and
refused are in [execution stats](/api/v1/docs/execution-stats).

## One line per step, keyed by unit and `seq`

`seq` counts the lines of one unit — a scenario in a backtest, the session in a live run — and
never repeats inside it, across both lists. The answer declares a key per list in `keys`: both
`events` and `broker_truth` are keyed `["scenario_name", "seq"]`; see
[row keys](/api/v1/docs/row-keys). Within a unit, `seq` IS the order of the stream, and merging
the two lists by it gives the order they were written in. Do not sort
by time: several steps often carry the same instant — a backtest's market order is taken and
filled at once — and a sort by time leaves their order to chance.

## `submitted_seq` ties the steps of one order together

`order_id` repeats: a position's open and its market closes share it, so it does not identify an
order. A protective order resting at the venue has an id of its own and names its position in
`position_id`; a close refused before it was sent is `close_<position id>`. Every event carries
`submitted_seq` — the `seq` of the submission it belongs to — and all the steps of one order share
that number. Group by `scenario_name` and `submitted_seq`.

An order a previous session sent and this one took over at its start has no submission here; its
`adopted` event stands in for it, and `submitted_seq` points there. A `denied` order was never
submitted and carries none.

## What happened — `event_type`

| `event_type` | Meaning | Backtest | Live session |
|---|---|---|---|
| `submitted` | handed to the venue | yes | yes |
| `accepted` | the venue took it: answered with its reference, started resting, or filled in its answer | yes | yes |
| `rejected` | the venue refused it — in a backtest the simulated venue | yes | yes |
| `denied` | refused before anything was sent (lot size, funds, the order guard) | yes | yes |
| `triggered` | a stop reached its price; a stop-limit becomes a limit | yes | not reported by the venue |
| `modify_requested` · `modified` · `modify_rejected` | a modification asked for, carried out, or refused — also refused when the order filled first | yes | yes |
| `cancel_requested` · `cancelled` · `cancel_rejected` | a cancel asked for, carried out, or refused — also refused when the order filled first | yes | yes |
| `cancel_deferred` | a cancel asked for before the venue had answered the order — it is sent once the answer arrives, unless the order filled or was refused in that answer | no | yes |
| `partially_filled` | part of the order executed | no | yes |
| `filled` | the order executed | yes | yes |
| `expired` | the order ran out of time — the end of a backtest's data, or the venue's expiry | yes | yes |
| `unresolved` · `resolved` | an answer that was lost, and the asking that settled it; `lost_request` names the request whose answer it was | no | yes |
| `undelivered` | the venue confirms it never received the order | no | yes |
| `unaccounted` | we stopped asking; the venue may still hold the order | no | yes |
| `adopted` | an order a previous session sent, taken over at this session's start | no | yes |

`initiator` and `end_reason` say who ended an order and why, `rejection_reason` why it was refused.
`venue_reason` is the venue's own code where it gave one, passed on as it came. `lost_request` is
one of `submit`, `cancel`, `modify` and `status_read` — the read with which an order that waited
too long for its fill is asked about. `record_plane` says whose account a line is: `bot` — what the
session did and was told — on every line of `events`, `broker_truth` on every line of the second
list, below.

## Two flows, as they read

```
Backtest, market buy (modelled delay 40 ms)
  seq 1  submitted   12:00:00.000   submission_mid 64210.5
  seq 2  accepted    12:00:00.040   in_flight_ms 40
  seq 3  filled      12:00:00.040   fill_price 64212.0
Live session, market buy
  seq 1  submitted   12:00:00.000
  seq 2  accepted    12:00:00.120   in_flight_ms 120
  seq 3  filled      12:00:01.900
```

A backtest's market order is taken and filled in one instant. A live venue answers with its
reference first, and the fill shows up later.

## One event, field by field

Every entry of `events` carries the same fields. Most are null on most events — a submission has
no fill price, a fill has no ending — and null means the value does not exist for this step, never
zero.

| field | what it says | null when |
|---|---|---|
| `scenario_name` | the unit the event belongs to: a scenario, or the session | never |
| `seq` | the event's place in its unit's stream — the order to read in, never a timestamp | never |
| `event_type` | what happened, above | never |
| `order_id` | the order's own id; a position's open and its market closes share one, a protective order has its own | never |
| `submitted_seq` | the `seq` of the submission this event belongs to — the join key for one order's steps | on `denied`, which was never submitted |
| `record_plane` | whose account this is: `bot` for this side's own steps | never |
| `position_id` | the position the order opens or closes | the order belongs to no position yet |
| `action` | `open` or `close` | not known for this step |
| `order_type` | the type the order was asked as | not known |
| `symbol` | the instrument | not known |
| `direction` | the POSITION's direction; a close carries the direction of the position it closes, which a protective order's own trading side is the opposite of | not known |
| `client_order_id` | this side's key on the wire | a backtest, or an order sent without one |
| `broker_ref` | the venue's handle for the order | a backtest, or the venue has not answered yet |
| `previous_broker_ref` | the handle an amend replaced | every event that is not an amend's answer |
| `trade_id` | the execution, where the event is exactly one | the event is no single execution |
| `lots` | this event's quantity — asked for, executed or ended | the step has none |
| `cum_lots` | what the order has executed so far | nothing executed yet, or not known |
| `fill_price` | the price the execution happened at | the event is no execution |
| `limit_price` | the order's limit | the order has none |
| `trigger_price` | the order's stop — the price that activates it | the order has none |
| `fee` · `fee_currency` | the fee charged on this execution, and its currency | the event is no execution, or the venue charged none |
| `submission_mid` | the market's mid when the order was submitted | every event but the submission |
| `submission_time_msc` | that market moment, in epoch milliseconds | as above |
| `in_flight_ms` | on the acceptance or refusal that answers a submission: how long the answer took — modelled in a backtest, measured live | every other event |
| `event_time` | when it happened, on the run's own clock | only before the run's clock was set — an order taken over at a session's start |
| `ts_init` | when this process saw it, on the wall clock | a backtest, which has to stay reproducible |
| `initiator` | who ended the order: `strategy`, `framework` or `venue` | the event ends nothing, or the order was refused |
| `end_reason` | why it ended | as above |
| `rejection_reason` | why it was refused | the event is no refusal |
| `venue_reason` | the venue's own code for a refusal, passed through unread | no venue code was given |
| `message` | the sentence that goes with it, for a person to read | there is none |
| `lost_request` | on `unresolved` and `resolved`: which request's answer was lost | every other event |

## What the venue said — `broker_truth`

A live session asks its venue what it holds, and each answer is one line of `broker_truth`: the
venue's whole account at that moment, never a step of one order. A backtest has none — its venue
is its own book.

| `read_reason` | When | What the line holds |
|---|---|---|
| `session_start` | before the session's first market data | the venue's open orders, its balances, and on a margin account its positions |
| `session_end` | after the session handled its own orders at the end | the same |
| `reconcile` | the session's comparison with the venue changed its picture | the open orders and positions of that comparison; the balances only when the picture turned between clean and divergent |

- `venue_orders` are the orders the venue reports as open — all of them, also orders this session
  did not place — each with the venue's `broker_ref`, our `client_order_id` where it carries one,
  its size, what has executed (`filled_lots`), its prices and its status. `venue_balances` is the
  venue's balance sheet, every asset, the quote currency included. `venue_positions` exist on a
  margin account only.
- **A part has three states.** A value — an empty one included — says what the venue holds: `[]`
  is "no open order". `null` with the part named in `unread_parts` says the venue could not be read.
  `null` without the name says this line does not read it: positions on a spot account, balances on
  a `reconcile` line that did not turn.
- A `reconcile` line says `reconcile_state` — `clean` or `divergent` — and, when divergent, names
  the members in `divergence`: venue references of orders the session cannot place
  (`ghost_orders`, `abandoned_orders`, `foreign_session_orders`), the session's order ids the venue
  does not show (`orphan_orders`, `unconfirmed_orders`) or shows differently (`stale_orders`), and
  positions as counts. Two such lines keep a configured distance — five minutes unless the session
  sets another: a change inside it is written once it has passed, if it still holds, so the list
  ends with the latest picture.
- Asked for one order (`order_id=`), this list is empty — no line in it is about one order.

```
seq 1    broker_truth  session_start  venue_orders []  venue_balances {USD 812.40, BTC 0.0031}
seq 2    submitted     pos_btcusd_1
...
seq 57   broker_truth  reconcile      divergent  ghost_orders [OQ3V2K-…]
seq 61   broker_truth  reconcile      clean                         ← at least five minutes later
...
seq 212  broker_truth  session_end    venue_balances null  unread_parts [venue_balances]
```

### The venue parts, field by field

These parts have a fixed shape whether or not a run you hold has ever filled them — type them
from here rather than from an instance.

**One entry of `venue_orders`** — an order the venue reported as open, in its own terms:

| Field | Meaning |
|---|---|
| `broker_ref` | the venue's handle for the order, always present |
| `client_order_id` | our wire key where the order carries one, else null |
| `symbol` | the instrument |
| `direction` | `long` or `short` |
| `order_type` | `market` · `limit` · `stop` · `stop_limit` · `trailing_stop` · `iceberg` · `unknown` |
| `lots` | the size as asked, not what remains |
| `filled_lots` | what has executed so far, `0.0` when nothing has |
| `limit_price` | the price it would fill at, null where the order has none |
| `stop_price` | the price that activates it, null where the order has none |
| `status` | `pending` · `filled` · `partially_filled` · `rejected` · `cancelled` · `expired` · `unresolved` · `unknown` |

**One entry of `venue_positions`** — margin accounts only: `symbol`, `direction`, `lots`,
`entry_price`, and `broker_ref` where the venue names the position.

**`divergence`** — on a divergent `reconcile` line only. Lists of order identities, each
possibly empty: `ghost_orders`, `abandoned_orders` and `foreign_session_orders` hold the venue's
references; `unconfirmed_orders`, `orphan_orders` and `stale_orders` hold this session's order ids.
Integers count positions: `ghost_positions`, `orphan_positions`, `stale_positions`.

`venue_balances` is an object from asset to amount, every asset the venue reports.

## Times and durations

- `event_time` is when the step happened on the run's own clock: the replayed market time in a
  backtest, the session's clock live. It is null only on a line written before the session's first
  market data — an `adopted` order taken over at the start, and the `session_start` line.
- `ts_init` is when the session saw the step, on the machine's clock — live only, and null in a
  backtest, which has to come out the same every time it is run.
- `in_flight_ms` is on the `accepted` or `rejected` that answers a submission: how long the answer
  took. A backtest's is the delay it modelled; a live one is measured when the session reads the
  answer, which can be up to one heartbeat after it arrived. An acceptance learned later by asking
  carries none — that span would be the asking, not the venue — and so does a backtest's exit at a
  protective level, which executes on the tick that reaches the level.

## Where this differs from order history

The order history keeps a row per submission and per ending; this stream keeps the steps. Where
the venue executed part of a resting order and then ended the rest, the history ends the order with
one row — the fill of what executed — and the stream has two steps: `partially_filled`, then
`cancelled`, `expired` or `rejected`.

## A run that is still going

A live session writes the stream as the steps happen, so it is served from the session's first
order, and a session that ended without its report still has it. A backtest writes its stream with
its report. The run list names it under `stream_files`. If the
session was stopped while a line was being written, that last line is left out and
`truncated_tail` is `true`; everything before it is complete.

## When there is no stream

A run from before the stream existed has none, and a backtest whose scenarios never placed an
order writes none. The answer is a 404 that names the cause, as for every section — see
[errors](/api/v1/docs/errors). A stream written in an earlier form of the stream — before the
venue's lines — is answered as unreadable (409) until its run is run again.
