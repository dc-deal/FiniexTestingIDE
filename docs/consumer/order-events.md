# Order events

[Order history](/api/v1/docs/order-history) keeps a row for each submission and one for every way an
order ENDED. What happened in between is missing there: when the venue took the order, when a stop
triggered, every cancel and modification that was asked for and how it was answered, an answer that
never came and the asking that settled it. This stream keeps all of it — one line per step in an
order's life, in the order the steps happened.

**Routes**

```
GET /api/v1/reports/runs/{run_id}/order-events
GET /api/v1/reports/runs/{run_id}/order-events?scenario_name=<unit>&order_id=<order>
```

**What is not here:** what a closed position earned — that is
[trade history](/api/v1/docs/trade-history). The per-unit counts of orders submitted, executed and
refused are in [execution stats](/api/v1/docs/execution-stats).

## One line per step, keyed by unit and `seq`

`seq` counts the events of one unit — a scenario in a backtest, the session in a live run — and
never repeats inside it. The list declares its key as `["scenario_name", "seq"]`; see
[row keys](/api/v1/docs/row-keys). Within a unit, `seq` IS the order of the stream. Do not sort
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
too long for its fill is asked about. `record_plane` says whose account a line is: `bot`, what the
session did and was told, on every line today; `broker_truth`, the venue's own account when it is
asked, is reserved for it.

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

## Times and durations

- `event_time` is when the step happened on the run's own clock: the replayed market time in a
  backtest, the session's clock live. It is null only for an `adopted` order taken over before the
  session's first market data.
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
[errors](/api/v1/docs/errors).
