# Loop Pass Order Tests

`tests/framework/loop_pass_order/` — what each tick loop calls in each kind of pass, and in which
order. Both loops are about to send their passes through one dispatcher (#458), a move that must
not change behaviour. A parity test compares what the two pipelines produce, but a step that moves
inside one pass can leave every output the same on a fixture and still change a real run: a drain
that comes after the ghost pass instead of before it, a boundary checked after the broker path
instead of before. This suite pins the call order itself.

It was written against the loops before the move and is meant to stay unchanged through it. When
the passes later take the phase order of the event model (#460 for the backtest, #461 for the
AutoTrader), the differences become deliberate, and this suite is where they are written down.

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
| Backtest tick | clock → day boundary → broker `on_tick` → bars → equity sample → booking observation → market-data tracker → algo → execute → drain (only with a dispatcher) → state frame |
| Backtest clipped tick | clock → day boundary → broker `on_tick` → bars → equity sample → booking observation, and nothing after |
| Backtest ghost pass | clock → day boundary → executor heartbeat → drain → ghost compute → execute (only when the ghost decided something) → drain → state frame |
| AutoTrader tick | signal inbox → clock → day boundary → sealed days → log rotation → broker `on_tick` → market-data tracker → account value (booking observation, equity sample) → bars → algo → equity sample for safety → safety check → entries blocked? → execute → rejection record → drain → reconcile → state persistence → carry-over → state frame |
| AutoTrader idle pass | clock → day boundary → sealed days → log rotation → executor heartbeat → staleness → reconcile → signal inbox → drain → ghost compute → (entries blocked? → execute → rejection record, when the ghost decided something) → state persistence → carry-over → state frame — no drain after the ghost |
| A stop's order wait | clock → day boundary → sealed days → log rotation → executor heartbeat → drain (only before the session-end event) → carry-over |

Two differences between the pipelines show in the table and are kept on purpose until the phase
order arrives: the AutoTrader merges its signal inbox before the clock moves, and it samples the
account after the algo computes as well as before, where the backtest samples once, before its
clipping gate.

## What Is Tested

### `test_backtest_pass_order.py`

| Test | What it pins |
|---|---|
| `test_clock_boundary_broker_bars_equity_then_algo_execute_drain` | Three tick passes with a dispatcher and a frame recorder, then the end of the run: the session-end event and the outcomes of its orders each drained, and one more equity sample |
| `test_without_a_dispatcher_nothing_is_drained` | The same without a dispatcher: no drain in the pass, none at the end |
| `test_stops_after_the_equity_sample` | The clipped middle tick of three stops after the booking observation: no tracker, no algo, no execute, no drain, no frame |
| `test_clock_boundary_heartbeat_drain_ghost_execute_drain` | A heartbeat logic, ticks a second apart and a heartbeat every 400 ms: two ghost passes between the two ticks, the first executing its decision, the second computing none and executing nothing, each drained before and after |
| `test_none_in_a_gap_no_longer_than_the_interval` | A heartbeat every 1000 ms over the same gap: no ghost pass |
| `TestTheIndicatorSeries` (#379) | the indicator series is recorded right after the state frame on every tick pass, and with the series alone the bars a tick closed are still read |
| `test_keys_in_pass_order_and_counted_per_pass` | The profiling timers' keys in the order the pass reaches them, and their counts over three ticks with one clipped — the reports read both |

### `test_autotrader_pass_order.py`

| Test | What it pins |
|---|---|
| `test_inbox_before_the_clock_then_broker_account_bars_algo_safety_execute_drain` | Two tick passes, then the session-end event and the outcomes of its orders, each drained |
| `test_clock_boundary_heartbeat_staleness_reconcile_inbox_drain_then_ghost` | Two ticks, then two idle passes on a fixed wall clock a second later: the first ghost decision executes, the second is None, and neither is drained after |
| `test_clock_boundary_heartbeat_drain_carry_over` | A stop asked for before the first pass with a market buy on its way: no tick or idle pass, wait passes until the buy is settled, each heard, then the session end |
| `TestTheIndicatorSeries` (#379) | the indicator series right after the state frame on every tick pass; with the series alone the closed bars are still read, right after the bars render |
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

## Running the Tests

```bash
python -m pytest tests/framework/loop_pass_order/ -v
```

VS Code: `🧩 Pytest: Loop Pass Order (All)`.
