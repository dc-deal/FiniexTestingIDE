# Order history

[Trade history](/api/v1/docs/trade-history) shows what worked. It cannot show what was asked for
and refused, what was asked for twice, or what the venue never answered — so a strategy that
wanted twenty positions and was allowed three looks exactly like one that only ever wanted three.
This section is the order log: a row when an order is placed and a row for the way it ended,
including the endings that produced no trade.

**Routes**

```
GET /api/v1/reports/runs/{run_id}/order-history
```

**What is not here:** what a closed position earned — that is
[trade history](/api/v1/docs/trade-history). Orders that were still resting when the run's data
ended are in [pending orders](/api/v1/docs/pending-orders), the per-unit count of each way an order
ended is in [execution stats](/api/v1/docs/execution-stats), and every step between an order's
placing and its end — the venue taking it, a modification, an answer that was lost — is in
[order events](/api/v1/docs/order-events).

## A row is an event, not an order

One order appears several times. It is recorded `pending` when it is placed and once more for the
way it ended — `executed` when it fills — and every close of a position adds a row of its own: a
position closed in three parts produces three close rows.

The rows are in **append order within their unit**, and that position in the list is their
identity. No combination of fields is unique, so this list declares **no row key**, which is a
statement in itself rather than an omission: see [row keys](/api/v1/docs/row-keys). Keep the order
the API served, and do not deduplicate.

An order that ends without filling has a row for that ending too: its `status` says which way it
ended, and on a cancelled, expired or unaccounted row `initiator` and `end_reason` say who ended it
and why.

**A protective order is the exception, twice.** The stop or target this side places at the venue
for a position carries an `order_id` of its own — `protect_` and the position's id — and it is not
recorded `pending` when it is placed: its placement is in the
[order-event stream](/api/v1/docs/order-events), and the history records only how it ended.

**The last row under an `order_id` is not what became of the position.** It says how the latest of
the orders under that id ended. What the position became is in
[trade history](/api/v1/docs/trade-history) — each closed part, with `close_type` and
`position_closes` — and, for a position still open when the run ended, in
[portfolio](/api/v1/docs/portfolio) under `open_positions`. A position whose entry never executed
appears in neither.

## `order_id` is the position's id, so it repeats

It is minted when the position opens and carried by every later order belonging to that position,
a protective order excepted (above). It does not identify a row and it does not identify an order.

An executed row's `scenario_name` and `position_id` together name the same position as a trade
row does. One counter per unit mints it and no id is used twice, so that pair is a reliable join
into [trade history](/api/v1/docs/trade-history).

**A close row and the trade it produced share no key.** They are made in the same step, but
nothing yet connects them — do not pair them by position in the list and do not pair them by
matching their contents.

## What one row carries

| Field | Meaning |
|---|---|
| `scenario_name` | the run unit — a scenario in a backtest, the session in a live run |
| `order_id` | the id of the position this order belongs to |
| `position_id` | the position, once one exists |
| `symbol` | the instrument |
| `direction` | on an open, the direction requested; on a close, the direction of the position being closed |
| `action` | `open` — creates or extends a position · `close` — reduces or closes one |
| `status` | where the order stood at this event |
| `requested_lots` | what the strategy asked for |
| `executed_lots` · `executed_price` | what was actually filled, and at what price |
| `event_time` | when this row's event happened |
| `order_type` | the type the order was asked as — on every row, a refusal included |
| `close_type` | on a close row: whether it closed all of the position or part of it |
| `commission` | the fee this event cost |
| `rejection_reason` · `rejection_message` | why it was refused, as a code and as a sentence |
| `initiator` · `end_reason` | who ended the order, and why — on a cancelled, expired or unaccounted row |

`event_time` runs on the run's own clock, so in a backtest it is the replayed market time and not
the time the backtest was executed. It is the moment of the event this row records — the
submission on a `pending` row, else the fill, the refusal, the cancel, the expiry.

`order_type` is the type that was REQUESTED: a stop-limit order stays `stop_limit` after its stop
triggered, a close is `market`, and an exit through a protective order the venue held carries that
order's type.

`direction`, `action`, `status`, `order_type`, `close_type`, `rejection_reason`, `initiator` and
`end_reason` are closed vocabularies and appear in the schema with their values.

## What is null, and the one field that never is

A value that does not exist here is **null** — never an empty string and never `0.0`. A zero price
read as a price is a mistake that cannot be undone further down.

| Field | Null when |
|---|---|
| `position_id` | no position exists yet |
| `requested_lots` | the order did not know the size |
| `executed_lots` · `executed_price` | nothing executed |
| `close_type` | the row is not a close, or the close did not execute |
| `rejection_reason` · `rejection_message` | the order was neither denied nor rejected |
| `initiator` · `end_reason` | the row is not a cancel, an expiry or an order unaccounted for |
| `direction` · `action` · `order_type` | the record did not carry them |

The exception is `commission`: it is a plain number and present on every row. For the cost of a
*closed trade*, read the itemised fees in [trade history](/api/v1/docs/trade-history) rather than
summing these.

See [nulls](/api/v1/docs/nulls) for how to render an absence.

## `status`

`pending` is an order that has not ended — on its way, or resting at the venue. Every other value
is one way an order can end, and each has its own word because each is acted on differently:

| `status` | The order |
|---|---|
| `executed` | filled |
| `denied` | was refused before anything was sent — the run's own checks, the lot size, the funds at submission, a close for a position that is not held, a close held back |
| `rejected` | was refused by the venue — in a backtest the simulated one: its funds or margin check at the fill, a stress test |
| `cancelled` | was cancelled — on request, or by the venue |
| `expired` | ran out without filling — the end of a backtest's data, or the venue's own expiry |
| `undelivered` | never reached the venue, as the venue confirms |
| `unaccounted` | was given up on: the session stopped asking, and the venue may still hold it, filled or not |

`denied`, `rejected`, `undelivered` and `unaccounted` are the endings nobody planned for; the run
counts them together as `orders_failed` in [execution stats](/api/v1/docs/execution-stats).

The `status` query parameter takes one of these values and matches it exactly.

## Who ended it, and why — `initiator` and `end_reason`

On a cancelled, expired or unaccounted row, `initiator` says who ended the order — `strategy`,
`framework` (this side: a timeout, the end of the run, a protective order no longer needed) or
`venue` — and `end_reason` says why:

| `end_reason` | The order ended because |
|---|---|
| `cancel_requested` | the strategy asked for the cancel |
| `protection_released` | it was a protective order its position no longer needed at the venue: the position's close was going out, or its stop was withdrawn |
| `order_timeout` | it did not fill within the order timeout |
| `resolution_ceiling` | its answer was lost, and the venue never named it however long it was asked |
| `session_end` | the live session ended with the order still out |
| `scenario_end` | the backtest's data ended with the order still out |
| `venue_cancelled` | the venue cancelled it on its own |
| `venue_expired` | the venue let it expire |

## Rejections

A refusal carries its side and its symbol, which matters more than it sounds: it means a `symbol`
filter **keeps** the refusals for that instrument instead of silently dropping them.

`rejection_reason` is the code to branch on and `rejection_message` the sentence to show; both are
set on a `denied` and on a `rejected` row. The vocabulary:

```
insufficient_margin · insufficient_funds · invalid_lot_size · symbol_not_tradeable
market_closed · invalid_price · order_type_not_supported · broker_error
rejection_cooldown · stale_market_data · unaccounted_order · position_not_found
close_withheld · remainder_below_minimum
```

Four of them are worth spelling out, because their names do not carry their meaning:

- **`remainder_below_minimum`** — the size asked for is fine, the *leftover* is not. Closing it
  would strand a remainder below the instrument's minimum volume, which could never be sold
  afterwards. It is its own reason rather than `invalid_lot_size` precisely because the lots
  requested are valid, and a reader given the other name would look in the wrong place.
- **`unaccounted_order`** — a new entry was refused because an order this session sent is
  unaccounted for: the venue was asked about it as long as the session said it would ask, and
  never named it. **It may still be resting there.** Closing and protecting what is already held
  continue; only new entries stop.
- **`position_not_found`** — a close for a position that is not held. In a backtest the simulated
  venue also refuses a close whose position was closed while the close was on its way.
- **`close_withheld`** — the close was held back because the protective order resting over the
  position could not be cancelled first, and a close beside a working stop can fill twice.

An answer that never came is not a refusal: the [order events](/api/v1/docs/order-events) follow it
as `unresolved` and `resolved`, and an order whose answer was never found ends `unaccounted`. Which
reasons a run can produce depends on its kind — see [run kinds](/api/v1/docs/run-kinds).

## The filters

Two query parameters narrow the list: `symbol` and `status`, both exact matches on the value.

`count` and `symbols` are rebuilt over the rows that survive the filter, so `count` is the number
of rows served and not the number of events the run recorded.

## Retention: this list has a ceiling

A run keeps its order events in a buffer whose size the server that produced the run sets, and
which that server can also switch off. Where it is set and a run records more events than it
holds, the oldest are discarded and the run logs a warning the first time that happens —
[warnings and errors](/api/v1/docs/warnings-errors) is where a run's warnings are served.

The counts in [execution stats](/api/v1/docs/execution-stats) are kept on counters beside this
list rather than derived from it, so they do not shrink with it. Where the two disagree and no
filter is set, that difference is the buffer, and execution stats is the figure to trust for how
many orders were sent, executed and rejected.

## When there is no report

A run with no order-history artifact answers 404, and the refusal names which of four causes it
was: the run is unknown, it was started without reports, it has produced none yet, or it produced
other sections and not this one. See [errors](/api/v1/docs/errors).
