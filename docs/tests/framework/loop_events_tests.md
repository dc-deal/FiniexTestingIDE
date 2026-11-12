# Loop Event Tests

`tests/framework/loop_events/` — the event vocabulary of the loop core and its one timeline (#457,
the first step of #375). Nothing in either pipeline uses these units yet; both tick loops run as
before. What the suite holds is the contract the dispatcher (#458) and the two drivers will be
built on, so it is readable and tested in one place before anything depends on it.

Not here: what the dispatcher does with an instant — running its phases, stopping after the
current instant at a session end, skipping the algo pass of a clipped slice while its resolutions
still run. Those are the dispatcher's halves of #457's edges, tested with #458. This suite holds
the timeline's halves: an instant leaves whole, and a clipped slice leaves with the events beside
it.

## The contract in five sentences

- Every event carries two stamps, in UTC epoch milliseconds: `ts_event`, the stamp it carries from
  its source, and `ts_init`, the stamp this process gave it on receipt. A backtest orders by the
  first, a live session by the second (`EventOrdering.pick` is the one place that says so), and the
  other one travels as data.
- The timeline hands out the earliest stamp first, then the submission order (`seq`). No kind of
  event ever goes before another.
- Events sharing a stamp are one instant and leave together. The phases of an instant
  (`InstantPhase`) order the work inside it: the world moves first, so a fill due in the same
  millisecond as a tick fills at that tick's price.
- Time handed over never runs backwards: an event stamped before the last instant handed over is
  refused (`PastLoopEventError`). One stamped equal to it forms a later instant at that stamp.
- Ticks sharing the run's ordering stamp are one data slice: the broker path sees every one of
  them, in order, and a clipped tick stays in its slice.

## What Is Tested

### `test_loop_event_types.py`

| Test | What it pins |
|---|---|
| `test_the_kinds_and_the_event_classes_match_both_ways` | Each `EventKind` is carried by exactly one event class, and each class names a kind |
| `test_an_event_says_its_kind` | `event.kind` answers from the class |
| `test_every_ordering_picks_one_of_the_two` · `test_a_backtest_goes_by_the_source_stamp_and_a_live_session_by_its_receipt` | Every `EventOrdering` declares its stamp, and the two declare the right one |
| `test_an_event_cannot_be_changed` | Stamps and `seq` are final once an event is made |
| `test_a_slice_without_a_tick_is_refused` | An instant without data carries no data slice |
| `test_the_declaration_order_is_the_phase_order` | The seven phases of an instant, in the order #457 fixes them |
| `test_prices_are_applied_before_any_resolution` | Market data before broker resolutions before the algo pass |

### `test_event_timeline.py`

| Test | What it pins |
|---|---|
| `test_events_pushed_in_any_order_leave_in_stamp_order` | Fifty shuffles of one set of events, drained in the same order every time |
| `test_peek_leaves_the_event_in_place` · `test_an_empty_timeline_answers_nothing` | `peek` removes nothing; an empty timeline answers None and an empty instant |
| `test_the_instant_leaves_whole_in_submission_order` | A tick slice, a fill due and a timer at one stamp leave together, in the order they were made — the timeline's half of the session-end edge |
| `test_a_clipped_slice_leaves_with_the_fill_due_beside_it` | A clipped slice and a resolution at one stamp leave as one instant; clipping never drops an event |
| `test_no_kind_goes_before_another` | The same two kinds at one stamp, made in both orders: the submission order decides each time |
| `test_two_events_of_one_kind_at_one_stamp_leave_by_seq` | `pop` hands out equal stamps by `seq` |
| `test_a_backtest_orders_by_when_it_happened` · `test_a_live_session_orders_by_when_it_arrived` · `test_the_other_stamp_travels_as_data` | A fill executed at .300 and received at .450 against a timer due at .400: first in a backtest, second live, with both stamps kept |
| `test_a_live_instant_is_what_arrived_together` · `test_a_backtest_instant_is_what_happened_together` | With the two stamps disagreeing, an instant is cut by the receipt stamp live and by the source stamp in a backtest |
| `test_an_event_stamped_before_the_last_instant_is_refused` · `test_a_single_pop_counts_as_handed_over_too` | Once an instant is handed over, an earlier stamp is refused in both orderings and nothing is taken |
| `test_an_event_at_the_stamp_just_handed_over_forms_a_later_instant` | An equal stamp is taken and leaves as a later instant — what a dispatcher does with it is #458's to settle |

### `test_tick_slicer.py`

| Test | What it pins |
|---|---|
| `test_a_sweep_through_the_book_is_one_slice` | Three trades with one stamp — the archive's own example — become one slice holding all three prices in arrival order |
| `test_a_new_stamp_starts_a_new_slice` · `test_a_slice_never_reaches_back` | Consecutive equal stamps group; equal stamps that are not neighbours stay apart, because the input is a stream |
| `test_a_clipped_tick_stays_in_its_slice` | Clipping never drops data |
| `test_each_slice_takes_the_next_number` | A slice takes its `seq` from the run's sequencer when it is made |
| `test_a_backtest_groups_by_the_source_stamp` · `test_a_live_session_groups_by_the_receipt_stamp` | Two trades the venue stamped alike and received 40 ms apart: one slice by source stamp, two by receipt stamp |
| `test_a_receipt_slice_takes_the_latest_source_stamp_of_its_ticks` | A slice's stamp not ordering the run is the latest of its ticks', whatever order they came in |
| `test_the_source_stamp_is_the_venue_stamp_not_the_timestamp` · `test_a_tick_without_a_venue_stamp_takes_its_timestamp_to_the_millisecond` | A tick's source stamp is its venue stamp, otherwise its timestamp converted exactly |
| `test_the_receipt_stamp_is_the_collection_stamp` · `test_a_tick_recorded_without_its_arrival_was_received_when_it_happened` · `test_a_tick_with_neither_stamp_takes_its_timestamp_for_both` | A tick's receipt stamp is its collection stamp; without one, its source stamp stands in |

### `test_latency_due_peek.py`

The latency simulator answers when its next pending order comes due — the question a timeline asks
to place a resolution event — without resolving anything; and it decides which orders resolve at an
instant, and in which order, which is pinned beside it.

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
