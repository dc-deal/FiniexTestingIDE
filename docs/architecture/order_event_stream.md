# Order-Event Stream

The order history keeps a row for each submission and for every way an order ENDED, and nothing
about the way there: when the venue took the order, when a stop triggered, every cancel and
modification that was asked for and how it was answered, an answer that never came and the asking
that settled it. Without those steps a backtest and a live session cannot be compared transition by
transition — only by their endings, which is where their differences have already been folded away.
The order-event stream records every transition of every order, in the order it happened, in both
pipelines, under one vocabulary.

**Not here:** how a consumer reads the stream over the API —
[Order events](../consumer/order-events.md). How an order's life is modelled (in flight, resting,
the three worlds) — [Pending Order Lifecycle](pending_order_architecture.md). How endings become
rows and counts — [Reporting Pipeline](reporting_pipeline.md).

---

## One record per transition

`OrderEvent` (`python/framework/types/trading_env_types/order_event_types.py`) is one line of the
stream. The fields fall into groups:

| Group | Fields | What it says |
|---|---|---|
| Identity and order | `seq`, `submitted_seq`, `event_type`, `record_plane`, `order_id`, `position_id`, `action`, `order_type`, `symbol`, `direction`, `client_order_id`, `broker_ref`, `previous_broker_ref` | which order, which step, in which place of the unit's stream |
| Quantities and prices | `lots`, `cum_lots`, `trade_id`, `fill_price`, `limit_price`, `trigger_price`, `fee`, `fee_currency`, `submission_mid`, `submission_time_msc` | what was asked, executed or ended, and at what price |
| Times | `event_time`, `ts_init`, `in_flight_ms` | when it happened on the canonical clock, when this process saw it, how long an answer took |
| Why | `initiator`, `end_reason`, `rejection_reason`, `venue_reason`, `message`, `lost_request` | who ended or asked, why, and the venue's own word |

These carry the stream's structure:

- **`seq`** is strictly increasing within a run unit and IS the order of the stream. A timestamp is
  never the order: several steps often carry the same instant.
- **`submitted_seq`** is the `seq` of the submission an event belongs to — of the adoption, for an
  order a previous session sent. It is the join key, because `order_id` repeats across the closes of
  one position. Only a `denied` order, never submitted, has none.
- **`ts_init`** is the wall clock, live only. A backtest leaves it empty so that two identical runs
  write identical streams.

## What each pipeline writes

The vocabulary (`OrderEventType`) follows FIX execution reports and nautilus_trader's order events:
the step is named, never inferred from where the order stands. Two declared maps sit beside it, and
a test holds both to their subjects in both directions:

- `ORDER_EVENT_BY_STATUS` — which event each ENDING status is recorded as. Every ending goes through
  one booking point, so a row and its event cannot part.
- `ORDER_EVENT_PIPELINES` — which pipeline writes each member, and why the other does not: the
  simulation refuses a cancel of an order still on its way where live parks it (no
  `cancel_deferred`), the simulation never fills part of an order (no `partially_filled`), no venue
  traded today reports a trigger (no live `triggered`), and a backtest has no transport to lose an
  answer on and no orders to adopt.

The consumer document carries the table of members with both columns.

## Where events are recorded

All of it happens in the executors, on the main thread, with no formatting and on the canonical
clock — the same constraints as the fill path it sits in. Live adds the wall-clock receipt stamp
and an answer's measured duration.

```
                       AbstractTradeExecutor
  _record_submission ──┐   (count + submitted)
  _record_adoption ────┤   (count + adopted, marks the order accepted)
  _record_acceptance ──┼──► _record_order_event ──► seq += 1 ──► listeners
  _book_order_result ──┤   (row + count + ending; accepted first before a fill)
  explicit steps ──────┘   (triggered · cancel/modify requested · cancel/modify rejected ·
                            modified · unresolved · resolved · partial endings)
                                                          │
                     backtest: a list in the scenario ◄───┤
                     live session: OrderEventStreamWriter ◄┘ (one flushed line each)
```

- **Endings** — every row booked through `_book_order_result` with a status other than `pending`
  becomes an event. Passing the order is what gives the event its join key, so every booking that
  has an order passes it.
- **Submissions and adoptions** — `_record_submission` and `_record_adoption` count and record in
  one statement, so `orders_submitted` and `orders_adopted` are, by construction, the number of
  `submitted` and `adopted` events.
- **Acceptance** — once per order, before its first fill. Recorded where an order starts resting
  (the simulation on arrival, live on the submit answer that names it), on a live market order's
  submit answer (a request processor hook), and late — without a latency — where the reference
  arrives by asking or by the reconciliation's truth pull. A fill that finds no acceptance recorded
  records one first: the venue took every order it fills. An adoption counts as taken.
- **Lost answers** (live) — `unresolved` where a write's answer was lost or named nothing, with the
  `lost_request` it was about; `resolved` where the asking or the truth pull names the order. An
  absent verdict ends the order `undelivered`, the resolution ceiling ends a market order or close
  `unaccounted`, and a resting order at the ceiling is kept — its question stays open in the stream.
- **A venue ending after part of an order executed** (live) — the order history ends the order with
  one row, the fill of what executed; the stream records `partially_filled`, then the ending as an
  event of its own with `cum_lots` and no row.
- **A request the market overtook** — a cancel or a modification still on its way when the order
  fills — is answered refused, then the fill follows, in both pipelines: a backtest's fill is due
  before the request, and live the venue's refusal arrives after the fill and finds no order to
  name.
- **A cancel the framework sent** is recorded on the order like the strategy's, so the ending says
  who asked even when the venue reports it cancelled — the fill timeout's own cancel included.
- **Local refusals** of a cancel or a modification — busy, not found, not yet confirmed — write
  nothing: the refusal is the method's return value, as a `denied` order was never `submitted`.

## Storage

The stream is `io/order_events.jsonl` in the run's directory. Its first line names the schema once
(`record_kind: header`, `schema_version`, `run_id`), the way a file of many records written by one
writer states the writer's schema once rather than on every record; every later line is the
API's `OrderEventRow`, so the file and the route cannot describe an event differently. Empty
fields are left out of a line.

- **Backtest** — a listener in each scenario subprocess appends to a list, which travels back in
  `ProcessTickLoopResult.order_events` and `RunUnit.order_events`. `BatchReportCoordinator` writes
  every unit's events at report time, unit after unit. A backtest whose scenarios placed no order
  writes no stream.
- **Live session** — `open_order_event_stream`
  (`python/framework/autotrader/order_event_stream_setup.py`) opens the writer after the pipeline is
  built and before the cold start, whose adoptions are the first steps a session can record,
  registers it as an executor listener, and names the stream in the run index. Every event is
  written and flushed as it is recorded; the writer closes after the session's order cleanup, whose
  cancels are steps too. A session killed between two events loses nothing written; one killed
  during a line loses that line, and the reader reports it. A write that fails — a full disk — is
  reported once on the session's channel and ends the stream there; it never ends the fill it was
  recorded in, or the session.

The run index lists streams under `stream_files`, never under `artifacts`. An empty artifact list is
how a run that never reached its report is told apart from one that did, and a stream written from
the first order on would otherwise make every killed session look finished.

## Reading it back

`ReportStore.get_order_events` reads the file whenever it exists — a running session's stream grows
from its first order — and filters by unit and by order. A last line without its newline that does
not parse is a session killed mid-write: it is left out and `truncated_tail` says so. A broken line
anywhere else, or a header naming a schema this reader does not know, is an unreadable file
(`artifact_unreadable`), never a server error.

## What is derived from it

- **The pending-order counters** — per submission, the first event that ends its in-flight phase,
  as `IN_FLIGHT_ENDING_BY_EVENT` declares it, folded by the pending-orders report builder. A live
  session's stream is read back once the session has ended; a backtest's events come back from its
  scenarios. See [Execution Layer](architecture_execution_layer.md#pending-order-statistics).
- **A completeness check** — `order_event_stream_incomplete` compares the stream's submissions with
  the executor's own count, in both pipelines. Both come from one statement, so a difference is a
  record the stream lost on its way.

## Determinism

Two identical backtests write identical streams, field for field — a test runs one twice and
compares, and the order guard's numbering has a test of its own. What keeps it so: nothing in the simulation's event path reads the wall clock
(`ts_init` is empty there), the order guard numbers its denials with a counter rather than a random
suffix, and every event is recorded on the one thread that advances `seq`.

## Memory

Measured 2026-10-06: one event holds about 476 bytes. The largest stored scenario booked 1,054
order rows; at about five events per order that is about 2.5 MB per scenario, and about 250 MB
across 99 parallel scenarios, plus a transient copy when a scenario hands its result back. A live
session holds no list — each event goes to disk as it is recorded.

## Known limits

- `ts_init` is the moment the main thread records the step — up to one heartbeat after the worker
  received the venue's answer. The exact receipt stamp belongs to the event-loop rebuild (#457).
  Live `in_flight_ms` is measured at the same moment, so it includes that wait too.
- Live `partially_filled` is recorded only where a partial is booked: a protective order filled in
  steps, and a venue ending after partial execution. A resting entry that fills in parts is observed
  only when it fills (#342).
- The `broker_truth` record plane is declared and not yet written; the venue's own account of an
  order follows in the same stream.
