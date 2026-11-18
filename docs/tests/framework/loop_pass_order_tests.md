# Loop Pass Order Tests

`tests/framework/loop_pass_order/` — what each tick loop calls in each kind of pass, and in which
order. Both loops send their passes through one dispatcher (#458), a move that was not to change
behaviour. A parity test compares what the two pipelines produce, but a step that moves
inside one pass can leave every output the same on a fixture and still change a real run: a drain
that comes after the ghost pass instead of before it, a boundary checked after the broker path
instead of before. This suite pins the call order itself.

It was written against the loops before the move and stayed unchanged through it. With the
timeline driver (#460) the backtest's instants took the phase order of the event model, and the
differences are written down below and in the test's own docstring; the AutoTrader's follow with
#461.

**What #460 changed in the backtest, deliberately:**
- the clock is set once per instant, and a tick no longer sets it again;
- a modify or cancel due by the instant applies before its first tick (`scheduled`), and the
  latency queue resolves once after its last tick (`due`) — no longer inside the first tick's
  broker path;
- the bars a slice closed are read after all its ticks;
- the decision events are delivered before the algo as well as after it, so a fill of this
  instant reaches the hooks before the decision does;
- a heartbeat instant resolves through the same two calls instead of the executor's heartbeat;
- a slice of several ticks applies each of them, and the algo computes once.

Not here: what the steps compute. The booking parity, the bar parity and the suites of each
collaborator hold that; this suite only asks which step comes when.

## How a step is recorded

A spy is patched onto the CLASS of a collaborator the loop really uses — the executor, the booking
recorder, the bar controller, the portfolio, the market-data tracker, the decision event
dispatcher, the decision logic, the safety breaker, the log rotator — and appends a short step name
before it calls the real method. The run therefore behaves as it would without the spies. Where a
step is the AutoTrader loop's own work (the signal inbox, the sealed days, the account value, a
rejection, the reconcile, the staleness, the persistence), the spy sits on the loop's method that
does it. Only the orchestrator and the state-frame recorder are doubles: the first already is one
in the harness, and of the second only its position matters.

Each test compares the whole run's steps with a list written out in the test, so a step that is
missing, moved or doubled fails it, and so does one that appears where none was. The runs are
built with the booking-day harness (`tests/shared/booking_day_harness.py`) over a few flat ticks
one second apart; the suite runs in about a second.

## The orders pinned

| Pass | Steps, in order |
|---|---|
| Backtest slice | clock → day boundary → scheduled modifies and cancels → for each tick: broker `apply_tick` → bars → equity sample → booking observation; then the closed bars → what came due → drain → market-data tracker → algo → execute → drain → state frame (the drains only with a dispatcher) |
| Backtest clipped slice | the same up to the first drain, and nothing after |
| Backtest ghost pass | clock → day boundary → scheduled modifies and cancels → what came due → drain → ghost compute → execute (only when the ghost decided something) → drain → state frame |
| AutoTrader slice (#461) | clock → day boundary → sealed days → log rotation → for each tick: broker `apply_tick` → bars → market-data tracker → account value (booking observation, equity sample); then the closed bars → signal inbox → what the venue answered (`resolve_due`) → drain → algo → equity sample for safety → safety check → entries blocked? → execute → rejection record → drain → reconcile and state persistence by the tick count → carry-over → state frame |
| AutoTrader heartbeat (#461) | clock → day boundary → sealed days → log rotation → signal inbox → what the venue answered (the heartbeat's resolution) → staleness → drain → ghost compute → (entries blocked? → execute → rejection record, when the ghost decided something) → drain → carry-over → state frame |
| A stop's order wait | clock → day boundary → sealed days → log rotation → executor heartbeat → drain (only before the session-end event) → carry-over |

Since #461 both pipelines run an instant in the phase order of the event model. Against the
AutoTrader's order before it, seven steps moved on purpose:

| Step | Before #461 | Since #461 |
|---|---|---|
| signal inbox, tick | first, before the clock moves | after the slice's ticks and bars |
| bars | after the market status and the account value | before them, for each tick |
| the venue's answers, tick | inside the tick's broker path (`on_tick`) | once, after all of the slice's ticks (`resolve_due`) |
| decision events, tick | delivered only after the algo | before the algo as well |
| signal inbox, heartbeat | after the staleness and the reconcile | before the venue's answers |
| decision events, heartbeat | before the ghost pass only | after it as well |
| reconcile and state persistence, quiet market | on every idle pass, behind their gates | on timers of their own; by the tick count after a slice |

What stays the AutoTrader's own: the account is sampled again after the algo for the safety layer,
which may turn a decision into FLAT, and a pass has no clipping gate — a session measures how far
it falls behind and does not skip.

## What Is Tested

### `test_backtest_pass_order.py`

| Test | What it pins |
|---|---|
| `test_clock_boundary_broker_bars_equity_then_algo_execute_drain` | Three slices of one tick with a dispatcher and a frame recorder, then the end of the run: the session-end event and the outcomes of its orders each drained, and one more equity sample |
| `test_without_a_dispatcher_nothing_is_drained` | The same without a dispatcher: no drain in the pass, none at the end |
| `test_every_tick_reaches_the_broker_and_the_bars_and_the_algo_computes_once` | Two ticks arriving in one millisecond and a third a second later: two instants, the first applying both ticks and computing once |
| `test_the_algo_computes_on_the_slice_s_last_tick_for_both` | The same ticks at two prices: the algo computes on the second of the two, standing for both (740.a), then on the third |
| `test_stops_after_the_delivery` | The clipped middle tick of three still reaches the broker, the bars, the resolutions and the delivery, and nothing after: no tracker, no algo, no execute, no frame |
| `test_clock_boundary_heartbeat_drain_ghost_execute_drain` | A heartbeat logic, ticks a second apart and a heartbeat every 400 ms: two ghost passes between the two ticks, the first executing its decision, the second computing none and executing nothing, each drained before and after |
| `test_none_in_a_gap_no_longer_than_the_interval` | A heartbeat every 1000 ms over the same gap: no ghost pass |
| `TestTheIndicatorSeries` (#379) | the indicator series is recorded right after the state frame on every tick pass, and with the series alone the bars a tick closed are still read |
| `test_keys_in_pass_order_and_counted_per_pass` | The profiling timers' keys in the order the pass reaches them, and their counts over three ticks with one clipped: three for the broker path and the bars, two for the algo path, five deliveries, and the two algo passes timed whole (#460) — the reports read both |

### `test_autotrader_pass_order.py`

| Test | What it pins |
|---|---|
| `test_clock_ticks_bars_account_then_signals_resolutions_drain_algo_execute_drain` | Two slices — a replay slices by arrival millisecond — then the session-end event and the outcomes of its orders, each drained |
| `test_clock_inbox_resolutions_staleness_drain_ghost_then_drain` | Two ticks, then two quiet heartbeats on a fixed wall clock a second later: the first ghost decision executes, the second is None, and both are drained after |
| `test_a_ghost_decision_executes_as_flat_while_entries_are_blocked` | With the safety layer blocking entries, a ghost pass's BUY reaches the logic as FLAT — the override the idle pass applies between the decision and its execution |
| `test_clock_boundary_heartbeat_drain_carry_over` | A stop asked for before the first pass with a market buy on its way: no tick or idle pass, wait passes until the buy is settled, each heard, then the session end |
| `TestABacklog` (#461) | live, three ticks waiting on the queue are one slice: each reaches the broker path, the algo computes once, on the newest, and the workers count one tick — a backlog's ticks behind the newest never reached the algorithm; a replay computes on each arrival |
| `TestASessionAHookEnds` (#461) | a hook that asks for the session end in the trailing drain leaves its pass's state frame and indicator series, and no second pass runs; one that asks for it in the delivery before the algo leaves no algo pass, no frame and no series — as a backtest's instant does; one that asks for it in a heartbeat's delivery leaves no ghost pass and no frame |
| `TestTheIndicatorSeries` (#379) | the indicator series right after the state frame on every slice; with the series alone the closed bars are still read, once after the slice's ticks |
| `test_after_the_session_end_event_nothing_is_heard` | Nothing on its way before the stop, an order sent from the session-end event: the second wait's passes drain nothing, and the outcomes of that order are delivered after it |

A stop's wait makes as many passes as the venue's answer takes to arrive, which is the venue's
timing and not the loop's; those two tests count the passes they find and require at least one,
each in the pinned order.

### `test_sim_pass_hooks.py`

The backtest runner's own steps inside the loop core's tick pass (`SimPassHooks`), tested on their
own with stand-in collaborators.

| Test | What it pins |
|---|---|
| `test_one_sample_serves_the_drawdown_and_the_booking_period` | One equity evaluation per tick, and the booking period observes exactly that figure |
| `test_with_profiling_the_sample_is_timed_under_its_own_key` · `test_without_profiling_nothing_is_timed` | The sample is timed under `equity_sample` when the backtest profiles, and nothing is recorded when it does not |
| `test_the_stress_window_moves_before_the_status_is_recorded` · `test_without_a_stress_window_the_status_carries_no_label` | The planned stale window advances before the tracker records the status, with the window's label; without a window the label is empty |
| `test_a_slice_of_several_ticks_counts_each_of_them` | A pass over a slice of several unclipped ticks counts each of them in the tracker, so its fresh and stale figures still add up to ticks (#460) |

## Running the Tests

```bash
python -m pytest tests/framework/loop_pass_order/ -v
```

VS Code: `🧩 Pytest: Loop Pass Order (All)`.
