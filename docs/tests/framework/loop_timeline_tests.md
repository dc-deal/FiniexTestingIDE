# Loop Timeline Tests

`tests/framework/loop_timeline/` — the backtest's timeline driver (#460): the intake that merges
the ticks, the simulator's due times and a heartbeat logic's timers into instants, and what the
simulator does inside one instant. Before #460 the backtest walked its tick array one tick at a
time, and an order that came due between two ticks filled at the next one; now the clock jumps to
the next event, and an order fills when it comes due.

Not here: the order of the steps in each kind of instant, spied on the real collaborators — that
is `tests/framework/loop_pass_order/`, which also pins a slice of several ticks computing once.
The vocabulary the intake hands over — the event types, the two stamps, the generic timeline — is
`tests/framework/loop_events/`. Whether a backtest's results stay the same against the other
pipeline is the parity suites' (`tests/parity/`).

## The contract in five sentences

- The backtest orders by when a tick arrived: its recorded arrival, or its venue stamp where its
  file recorded none it can trust (data preparation clears those). Ticks that arrived in the same
  millisecond are one slice.
- The simulator's next due time — a fill, a scheduled modify or cancel — is an instant of its own
  between two slices, or joins the slice that falls on its stamp. It is asked for again after
  every instant, so an order sent in one comes due in time — never in the instant it was sent in,
  because its latency is at least a millisecond.
- A heartbeat logic gets a timer every interval inside a quiet stretch no longer than the gap
  threshold, none across a longer one, and none at all without opting in.
- Inside an instant, a modify or cancel due by then applies first, every tick of the slice moves
  the market and checks the resting orders, and what the latency queue has due fills once, at the
  prices of the last tick.
- Nothing comes due after the last tick: the run ends where its data ends.

## What Is Tested

### `test_sim_event_intake.py`

The intake against a stand-in simulator that answers its due times and moves them on when an
instant runs.

| Test | What it pins |
|---|---|
| `test_the_ticks_that_arrived_in_one_millisecond_are_one_slice` | Ticks sharing an arrival millisecond leave as one slice |
| `test_a_slice_is_stamped_with_the_arrival_and_its_last_tick_s_venue_stamp` | A slice's ordering stamp is the arrival; its source stamp is its last tick's |
| `test_an_arrival_that_steps_back_joins_the_running_instant` | Where two files meet with the later one's first arrival before the earlier one's last, the tick joins the running instant: the clock only moves forward |
| `test_a_tick_without_an_arrival_orders_by_its_venue_stamp` | A tick whose arrival was cleared orders by its venue stamp |
| `test_the_last_tick_handed_over_is_named_by_its_index` | The drive learns where in the tick array the last slice ended |
| `test_a_due_time_between_two_slices_is_an_instant_of_its_own` | A due time between two slices is handed over at its own stamp |
| `test_a_due_time_on_a_slice_s_stamp_joins_the_slice` | A due time on a slice's stamp is one instant with it |
| `test_an_order_sent_in_an_instant_comes_due_before_a_later_one` | An order sent at +5 with a due time of +25 comes before one already standing at +80 |
| `test_nothing_comes_due_after_the_last_tick` | A due time past the last tick is never handed over |
| `test_a_due_time_left_standing_by_its_own_instant_is_not_handed_over_again` | A due time its own instant did not resolve waits for the next slice instead of repeating |
| `test_a_due_time_before_the_instant_handed_is_reported_once_and_counted` | A due time still standing after a later instant was handed over resolves late; the scenario's log gets one error with its stamps and how late (60 ms), and the intake counts every one |
| `test_an_order_sent_where_a_fill_resolved_comes_due_a_millisecond_later` · `test_so_does_one_the_algo_sends_in_a_slice_that_resolved` | An order sent with the least latency a run accepts, 1 ms, in an instant that already resolved something comes due 1 ms later, in an instant of its own before the next slice |
| `test_a_timer_every_interval_inside_a_quiet_stretch` | Timers at each interval between two slices |
| `test_none_across_a_stretch_longer_than_the_threshold` | No timers across a stretch longer than the gap threshold (#208) |
| `test_a_timer_and_a_due_time_on_one_stamp_are_one_instant` | A timer and a due time on one stamp leave together |

### `test_instant_resolution.py`

A real simulator, driven through one instant at a time in the dispatcher's order.

| Test | What it pins |
|---|---|
| `test_at_its_due_time_at_the_price_standing_then` | Sent at .000 with 40 ms of latency, a market order is not filled at .039, and fills at .040 at the ask of the last tick before; the position opens at .040 |
| `test_its_latency_counts_from_the_instant_it_was_sent_in` | Sent in an instant without a tick, an order's due time counts from that instant, not from the last tick |
| `test_without_a_cancel_the_tick_fills_it` · `test_a_cancel_due_in_the_instant_keeps_it_from_filling_there` | A resting limit the next tick would fill stays unfilled when its cancel is due in that instant, and is gone |
| `test_a_modify_due_in_the_instant_applies_before_the_tick_is_checked` | A limit moved from 95 to 85, due in the instant, meets the tick there at its new price: an ask of 91 would have filled the old one, and the order rests on at 85 |
| `test_fires_on_the_tick_that_reached_it` | Three ticks in one millisecond, only the second reaching a buy stop: the stop fires at that tick's ask |
| `test_on_the_timeline_the_tick_leaves_the_clock_at_the_instant` · `test_a_tick_handed_over_on_its_own_still_moves_the_clock_to_its_time` | A tick the venue stamped 40 ms after its recorded arrival leaves the clock at the instant on the timeline, while a tick handed to `on_tick` on its own still moves the clock to its time |
| `test_keeps_the_algo_from_running` | A hook asking for the session end while an instant's events are delivered keeps the algo from computing on the slice |

### `test_timeline_determinism.py`

A backtest through the real loop, built with the booking-day harness: 1,200 seeded ticks, a third
of them sharing a millisecond, each arriving 25 ms after its venue stamp, a seeded latency of 20 to
400 ms and a heartbeat logic's timer every 700 ms.

| Test | What it pins |
|---|---|
| `test_the_fixture_is_worth_comparing` | The run trades, a slice holds more than one tick, and an order fills between two ticks |
| `test_gives_the_same_orders_fills_and_trades` | Two runs record the same orders, order events, trades and booking periods |
| `test_an_instant_without_a_tick_times_its_resolutions_apart` | The resolutions of an instant without a tick are timed as `instant_resolution`, and `trade_simulator` counts exactly the 1,200 ticks |
| `test_a_clock_that_jumps_about_changes_nothing` | With `time.time`, `time.perf_counter` and `time.monotonic` jumping an hour back and forth, the run records exactly the same |
| `test_every_time_the_record_holds_lies_in_the_data_s_span` | Every instant in the orders, events, trades and booking periods lies inside the data's span — a wall-clock read would land at the time the test ran |

### `test_arrival_intervals.py`

The interval the profiling measures between arrivals, through the real loop with the booking-day
harness (#450).

| Test | What it pins |
|---|---|
| `test_the_ticks_of_one_millisecond_count_once` | Three ticks at +0, two at +5, one at +20 give the intervals 5 and 15 — one per slice, none inside one |
| `test_a_burst_leaves_the_p5_above_zero` | Ten ticks in four arrivals 10 ms apart give a P5 of 10 ms; counted per tick, six of nine gaps were 0 ms and the P5 was 0 |

## Running the Tests

```bash
python -m pytest tests/framework/loop_timeline/ -v
```

VS Code: `🧩 Pytest: Loop Timeline (All)`.
