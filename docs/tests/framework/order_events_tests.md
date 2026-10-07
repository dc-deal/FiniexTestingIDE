# Order Events Tests

`tests/framework/order_events/` — every order transition as one event of the order-event stream,
in both pipelines, and the stream as a file, in the run index and over the API. Runs as the
`framework/order_events` suite.

A backtest and a live session can only be compared step by step if both write the same steps under
the same names. Nothing failed loudly without this: a step with no event, a second acceptance, an
ending filed under no submission or a wall-clock stamp in a backtest each leave a stream that reads
fine and compares wrong. This suite pins the vocabulary to the code that writes it, then walks each
kind of order life in both pipelines. The design is in
[Order-Event Stream](../../architecture/order_event_stream.md).

Not here: the order history's rows and counts, which are the
[Order Endings Tests](order_endings_tests.md); the lost-answer resolution itself, which the
[Live Executor Tests](../autotrader/live_executor_tests.md) cover — here only what it writes.

## What Is Tested

### `test_order_event_declarations.py`

The two declared maps, held to their subjects in both directions, the source walk modelled on the
validation check catalog's.

| Test | Description |
|------|-------------|
| `test_every_status_has_an_answer` | `ORDER_EVENT_BY_STATUS` covers the whole status enum |
| `test_only_the_submission_row_ends_nothing` | `pending` is the one status recorded where the submission is counted, not as an ending |
| `test_every_ending_is_its_own_event` | no two statuses read as one ending |
| `test_every_member_is_declared` | `ORDER_EVENT_PIPELINES` covers the whole event vocabulary |
| `test_every_member_has_a_pipeline` | a member nothing writes would satisfy every other check |
| `test_every_declared_member_is_emitted` | per pipeline: each declared member is named in that pipeline's executor sources, or reached through a status it books |
| `test_a_pipeline_emits_nothing_undeclared` | per pipeline: its own files name no member declared for the other one only |

### `test_simulation_order_events.py`

Each order life in a backtest, read by submission; money-touching flows in both account models.

| Test | Description |
|------|-------------|
| `TestAMarketOrder` | `submitted → accepted → filled`; the acceptance carries the modelled delay; the submission carries the market it was sent into; the fill states what executed and what it cost |
| `TestARestingOrder` | a limit accepted on arrival and filled when the market comes; a strategy cancel asked for then carried out, with its initiator; a fill that overtakes a cancel or a modification refuses it first; a modification at its new price; a local refusal writes nothing; the data end expires resting and travelling orders alike |
| `TestAStopOrder` | a stop rests, `triggered`, then fills at the market; a stop-limit triggers and fills as a limit while every step names the submitted type |
| `TestAStopLimitWithARequestOnItsWay` | only a fill in the same pass overtakes a cancel; a limit that rests keeps the cancel; a modification that moves the stop is refused at the trigger and the limit keeps its own price, while one that leaves the stop alone applies to the limit |
| `TestAPositionsCloses` | a close is an order of its own and states its position and that position's direction; a breached stop-loss exits through an order of its own |
| `TestARefusal` | a denial was never submitted; a refused close names its position; a stress-test refusal answers the submission with its delay |
| `TestTheStreamItself` | `seq` strictly increasing, every step names a recorded submission, no wall clock, and two identical backtests write identical streams |

### `test_live_order_events.py`

Each order life in a live session against the mock venue.

| Test | Description |
|------|-------------|
| `TestAMarketOrder` | `submitted → accepted → filled` in both account models; the acceptance is measured and every step carries both times; a fill inside the answer is still accepted first; a refusal answers the submission with the venue's own reason; a denial was never submitted |
| `TestARestingOrder` | a cancel asked for then carried out; a cancel before the reference is `cancel_deferred` and sent on the answer; a modification; a local refusal of a modification writes nothing; a venue ending after a partial fill is `partially_filled` then the ending without a row; the session end cancels and says so |
| `TestALostAnswer` | `unresolved` then `resolved` and a late acceptance without latency when the venue names it; `undelivered` when it never had it; the truth pull naming it is a resolution too |
| `TestAnAdoptedOrder` | adopted, and its fill follows without a second acceptance — counted adopted, not submitted |
| `TestAProtectiveOrder` | the entry, the protective stop and the close as three lives: the stop is cancelled first, with its position, its position's side and `protection_released` |
| `TestTheStreamItself` | `seq` strictly increasing across orders; an answer for an order that is gone names how it ended |
| `TestTheSessionsPendingCounters` | the pending-order counters derived from a session's events — one accepted, one rejected, one never confirmed — add up, and only the answers carry a duration |

### `test_live_crossed_answers.py`

What the stream records where answers cross, time out or come back late.

| Test | Description |
|------|-------------|
| `TestAFillOvertakesARequest` | a poll's fill read before a cancel's or a modification's refusal: refused, then filled — as the simulation records it |
| `TestALateResolveAnswer` | the truth pull settled a lost answer while the resolution's own question was out: the late answer writes no second `resolved` |
| `TestACancelBeforeTheReference` | a cancel asked for twice before the reference arrived is parked once and sent once |
| `TestACancelTheVenueDidNotCarryOut` | a lost cancel the venue did not carry out explains no later ending: a later expiry is the venue's |
| `TestTheFillTimeoutsOwnCancel` | the timeout's cancel after a partial fill ends the order as the framework's; a lost cancel answer is the cancel's question, not the read's; the give-up keeps the venue's refusal of its cancel |
| `TestAProtectiveOrderPartlyFilled` | what executed while it worked is its ending: the cancel answer adds the step, no second row |
| `TestTheSessionEnd` | a session-end cancel whose answer was lost is `unresolved` before the order is `unaccounted` |

### `test_order_event_stream_io.py`

| Test | Description |
|------|-------------|
| `TestTheFile` | the header names the schema once; an event is on disk before the next is recorded; empty fields are left out; a failed write is said once and ends the stream without ending the fill; a backtest writes its units one after the other, and none at all when no order was placed |
| `TestReadingItBack` | a cut-off last line is left out and reported; a broken line in the middle, an unknown schema, a line that is not a JSON object and a byte that is not UTF-8 are unreadable |
| `TestTheSharedWriter` | one compact line per record, nothing after close, and the field study's lines unchanged by the move onto the shared writer |
| `TestTheRunIndexListsItAsAStream` | the stream is named while the run is going and never counted among the artifacts |

The route itself — every step in stream order with its key, the two filters, a cut-off line, a
damaged file, a run without a stream — is in the API suite, `TestTheOrderEventStream` in
`tests/framework/api/test_reports_endpoint.py`.

## Running

```bash
pytest tests/framework/order_events/ -v
```

Fixtures and helpers (`record_events`, `order_lives`, `order_steps`, `limit_order`,
`market_order`, `live_session`, `make_simulator`, `sim_tick`) are in the suite's `conftest.py`. The protective-order flow borrows `VenueHoldsProtectionMock` from the
protective-level suite, the venue that lets a stop rest.
