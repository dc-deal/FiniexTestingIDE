# Order history

[Trade history](/api/v1/docs/trade-history) shows what worked. It cannot show what was asked for
and refused, what was asked for twice, or what the venue never answered — so a strategy that
wanted twenty positions and was allowed three looks exactly like one that only ever wanted three.
This section is the order log: one row for every step in an order's life, including the steps that
produced no trade.

**Routes**

```
GET /api/v1/reports/runs/{run_id}/order-history
```

**What is not here:** what a closed position earned — that is
[trade history](/api/v1/docs/trade-history). Orders that were still resting when the run's data
ended are in [pending orders](/api/v1/docs/pending-orders), and the per-unit counts of orders sent,
executed and rejected are in [execution stats](/api/v1/docs/execution-stats).

## A row is an event, not an order

One order appears several times. It is recorded `pending` when it is created, `executed` when it
fills, and then once more per close — a position closed in three parts produces three close rows.

The rows are in **append order within their unit**, and that position in the list is their
identity. No combination of fields is unique, so this list declares **no row key**, which is a
statement in itself rather than an omission: see [row keys](/api/v1/docs/row-keys). Keep the order
the API served, and do not deduplicate.

An order the strategy cancels itself leaves no row at all — its `pending` row is the last thing
you will see of it.

## `order_id` is the position's id, so it repeats

It is minted when the position opens and carried by every later order belonging to that position.
It does not identify a row and it does not identify an order.

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

`event_time` runs on the run's own clock, so in a backtest it is the replayed market time and not
the time the backtest was executed. It is the moment of the event this row records — the
submission on a `pending` row, else the fill, the refusal, the expiry.

`order_type` is the type that was REQUESTED: a stop-limit order stays `stop_limit` after its stop
triggered, a close is `market`, and an exit through a protective order the venue held carries that
order's type.

`direction`, `action`, `status`, `order_type`, `close_type` and `rejection_reason` are closed
vocabularies and appear in the schema with their values.

## What is null, and the one field that never is

A value that does not exist here is **null** — never an empty string and never `0.0`. A zero price
read as a price is a mistake that cannot be undone further down.

| Field | Null when |
|---|---|
| `position_id` | no position exists yet |
| `requested_lots` | the order did not know the size |
| `executed_lots` · `executed_price` | nothing executed |
| `close_type` | the row is not a close, or the close did not execute |
| `rejection_reason` · `rejection_message` | the order was not rejected |
| `direction` · `action` · `order_type` | the record did not carry them |

The exception is `commission`: it is a plain number and present on every row. For the cost of a
*closed trade*, read the itemised fees in [trade history](/api/v1/docs/trade-history) rather than
summing these.

See [nulls](/api/v1/docs/nulls) for how to render an absence.

## `status`

The vocabulary is `pending`, `submitted`, `executed`, `partial`, `rejected`, `cancelled` and
`expired`. The sequence described above — `pending`, then `executed`, then a close row per
close — is what an ordinary order's rows look like; a refused order appears as `rejected`, and an
order still resting when a backtest's data ends is recorded `expired` in that same step.

The `status` query parameter takes one of these values and matches it exactly.

## Rejections

A rejection carries its side and its symbol, which matters more than it sounds: it means a
`symbol` filter **keeps** the refusals for that instrument instead of silently dropping them.

`rejection_reason` is the code to branch on and `rejection_message` the sentence to show. The
vocabulary:

```
insufficient_margin · insufficient_funds · invalid_lot_size · symbol_not_tradeable
market_closed · invalid_price · order_type_not_supported · broker_error
rejection_cooldown · stale_market_data · broker_unreachable · unresolved_write
remainder_below_minimum
```

Three of them are worth spelling out, because their names do not carry their meaning:

- **`remainder_below_minimum`** — the size asked for is fine, the *leftover* is not. Closing it
  would strand a remainder below the instrument's minimum volume, which could never be sold
  afterwards. It is its own reason rather than `invalid_lot_size` precisely because the lots
  requested are valid, and a reader given the other name would look in the wrong place.
- **`broker_unreachable`** — the venue never answered before the order's timeout ran out. We did
  not reach it, so it refused nothing, and **it may still be holding the order**. This is our
  transport failing, not the venue declining.
- **`unresolved_write`** — a new entry was refused because an order in exactly that unresolved
  state is outstanding. Closing and protecting what is already held continue; only new entries
  stop.

The last two occur in AutoTrader sessions only — see [run kinds](/api/v1/docs/run-kinds) for which
kind of run you are holding.

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
