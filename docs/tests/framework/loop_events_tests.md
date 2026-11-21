# Loop Event Tests

`tests/framework/loop_events/` — the event vocabulary of the loop core (#457, the first step of
#375): the kinds of event, the events that carry them, a tick's two stamps and the phases of an
instant. Both timeline drivers are built on it, the backtest's (#460) and the AutoTrader's (#461).

Not here: how events are merged into instants and handed over — that is each pipeline's intake,
tested in `tests/framework/loop_timeline/` (`docs/tests/framework/loop_timeline_tests.md`) — nor
what the dispatcher does with an instant, which `tests/framework/loop_pass_order/` pins step by
step.

## The contract in four sentences

- Every event carries two stamps, in UTC epoch milliseconds: `ts_event`, the stamp it carries from
  its source, and `ts_init`, the stamp this process gave it on receipt. Both pipelines order by the
  second — a backtest by the receipt it recorded, a live session by the wall clock at arrival — and
  the first travels as data.
- A tick's two stamps are read in one place, `tick_event_msc` and `tick_receipt_msc` beside
  `TickData`; a file that recorded no arrival falls back to the venue stamp.
- Events sharing a stamp are one instant. The phases of an instant (`InstantPhase`) order the work
  inside it: the world moves first, so a fill due in the same millisecond as a tick fills at that
  tick's price.
- A data slice carries at least one tick; a clipped tick stays in its slice.

## What Is Tested

### `test_loop_event_types.py`

| Test | What it pins |
|---|---|
| `test_the_kinds_and_the_event_classes_match_both_ways` | Each `EventKind` is carried by exactly one event class, and each class names a kind |
| `test_an_event_says_its_kind` | `event.kind` answers from the class |
| `test_a_slice_without_a_tick_is_refused` | An instant without data carries no data slice |
| `test_the_declaration_order_is_the_phase_order` | The seven phases of an instant, in the order #457 fixes them |
| `test_prices_are_applied_before_any_resolution` | Market data before broker resolutions before the algo pass |

### `test_tick_stamps.py`

| Test | What it pins |
|---|---|
| `test_the_source_stamp_is_the_venue_stamp_not_the_timestamp` · `test_a_tick_without_a_venue_stamp_takes_its_timestamp_to_the_millisecond` | A tick's source stamp is its venue stamp, otherwise its timestamp converted exactly |
| `test_the_receipt_stamp_is_the_collection_stamp` · `test_a_tick_recorded_without_its_arrival_was_received_when_it_happened` · `test_a_tick_with_neither_stamp_takes_its_timestamp_for_both` | A tick's receipt stamp is its collection stamp; without one, its source stamp stands in |

### `test_latency_due_peek.py`

The latency simulator answers when its next pending order comes due — the question the backtest's
intake asks to place a resolution event — without resolving anything; and it decides which orders
resolve at an instant, and in which order, which is pinned beside it.

| Test | What it pins |
|---|---|
| `test_nothing_pending_is_nothing_due` | None without a pending order |
| `test_the_earliest_due_not_the_earliest_placed` | An open placed first with a long latency and a close placed later with a short one: the close's due instant |
| `test_asking_resolves_nothing` | Asking twice leaves both orders pending, in their order |
| `test_the_answer_moves_on_once_an_order_resolves` | Nothing resolves before its due instant; after one resolves, the next one's instant |
| `test_an_order_sent_through_the_simulator_is_due_after_its_latency` | The seeded path end to end: a market open is due within its latency range |
| `test_the_order_sent_first_resolves_first` | Two orders due in one millisecond resolve in the order they were sent, not by their ids |

## Running the Tests

```bash
python -m pytest tests/framework/loop_events/ -v
```

VS Code: `🧩 Pytest: Loop Events (All)`.
