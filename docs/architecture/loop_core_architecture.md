# The Loop Core

Both pipelines run the same strategy over the same kind of events — ticks, timers, fills coming
due — but each tick loop used to decide on its own what runs in which order. That is how the two
drifted apart before (the clipping gate once ran before the bars in one loop and after them in the
other). The loop core gives the order one home: an event model with one timeline, and one unit
that owns the order of every pass, fed by each pipeline's own drive.

This document covers the event model, the dispatcher and the two timelines that feed it. Not here:
what the executor does inside a step ([Execution Layer](architecture_execution_layer.md)), the two
drives side by side ([Simulation vs AutoTrader Flow](simulation_vs_autotrader_flow.md)), and what the
live session's clock means for decision logic ([Live Execution](live_execution_architecture.md)).

## The event model

An event is one thing that happened, placed on the loop's timeline (`types/loop_types/`):

| Kind | Event | What it carries |
|---|---|---|
| Market data | `DataSliceEvent` | the ticks of one instant — the broker path sees each of them, the algo computes once |
| Time | `TimeEvent` | a timer or a heartbeat falling due, with or without data at that instant |
| Resolution | `ResolutionEvent` | a fill or a broker answer coming due |
| Signal | `SignalEvent` | a signal snapshot becoming visible |

Every event carries two stamps in UTC epoch milliseconds — `ts_event`, the stamp it carries from
its source, and `ts_init`, the stamp this process gave it on receipt — and a submission number
`seq`. Both pipelines order by `ts_init`: a live session by its own receipt, a backtest by the
receipt it recorded (#460) — by the venue stamp only where a file records no arrival it can trust —
so the algo sees a tick when it would have seen it live, the way nautilus_trader orders its
backtests, by when a datum became available. A tick's two stamps are read in one place,
`tick_event_msc` and `tick_receipt_msc` beside `TickData`, and the venue stamp travels as data.

Each pipeline's intake hands out the earliest stamp first and never one kind before another.
Events sharing a stamp form one **instant** and leave together; the phases of an instant
(`InstantPhase`) order the work inside it — market data first, so a fill due in the same
millisecond as a tick fills at that tick's price. Time handed over never runs back: an event read
later with an earlier stamp joins the running instant.

Events are dataclasses with slots and are not frozen; nothing writes one after it is made. One is
built for every instant a backtest replays, and the frozen form cost more than the loop core's
whole budget per tick (measured with `python/experiments/cost_probes/probe_loop_dispatch_cost.py`).

## The dispatcher

`loop/loop_event_dispatcher.py` holds the order of a pass as code — one entry per kind of pass,
never a table, because a table hides its conditions and invites reordering at runtime. A runner
keeps its drive and hands each pass to an entry:

```
backtest drive (process_event_loop)              loop core
  for instant in SimEventIntake(…): ───────▶  dispatch_backtest_instant(events, simulator, ctx,
      state frame · series · session end                                 hooks, record)
      · live export                               clock → day boundary → scheduled modifies and
                                                  cancels → for each tick: broker path, bars,
                                                  equity sample → closed bars → what came due
                                                  → drain → clipping gate → stress + tracker
                                                  → algo, once → execute → drain
                                                  — at a timer: ghost → execute → drain

AutoTrader drive (AutotraderEventLoop.run)
  for instant in LiveEventIntake(…): ──────▶  dispatch_live_instant(events, executor, ctx,
      reconcile and persistence gates                                hooks, record)
      · timers armed again · clipping             clock → day boundary → for each tick: broker
      monitor · display · state frame             path, bars, market status + account value
      · series · session end                      → closed bars → signal inbox → what the venue
                                                  answered → staleness, reconcile at their
                                                  timers → drain → algo, once → safety
                                                  → safety override → execute → drain
                                                  — at the heartbeat: ghost → safety override
                                                  → execute → drain
  a stop's wait, per pass ─────────────────▶  dispatch_live_order_wait(now, ctx, hooks, hear)
                                                  clock → day boundary → heartbeat → drain
```

What a pass works with is bundled once per run:

- **`LoopContext`** — the run's collaborators: executor, bar controller, orchestrator, decision
  logic, the decision-event channel, the booking recorder and the profiling timers. A pass reads
  its fields directly, because it runs for every tick.
- **The blocks** (`loop/blocks/`) — the steps both pipelines share: rendering a tick's bars,
  computing the workers and the decision, the ghost pass's decision.
- **The hooks** — the runner's own steps inside a pass, at fixed points
  (`AbstractBacktestPassHooks`, which `SimPassHooks` implements; `AbstractLivePassHooks`, which
  the AutoTrader's event loop implements itself, because the steps are its own state). A hook
  exists only where the runner has work, so a pass makes no call that does nothing.
- **`LoopPassRecord`** — what a pass leaves for its drive: the ticks applied, the forming bars, the
  bars the tick closed, the decision and what it sent.

A pass ends with its last engine step. What a runner does after it stays in the runner, in its
order: the state frame, the session-end check, the live export, the timing — and in the AutoTrader
the reconcile and persistence gates, the clipping monitor and the display.

### One order in both pipelines

Both drives run their instants in the phase order of the event model — the backtest since #460,
the AutoTrader since #461 — and the tests in `tests/framework/loop_pass_order/` pin each step by
step. What stays different is what only one of them has: the backtest's clipping gate, which skips
the algo on a clipped slice, where a session measures how far it falls behind and never skips; and
the AutoTrader's safety layer, which samples the account after the algo and may turn a decision
into FLAT, its staleness check at the heartbeat and its reconcile at its timer.

## The backtest's timeline (#460)

The backtest's drive is a next-event clock: it jumps from one event to the next rather than walking
its tick array one tick at a time. The intake (`loop/intake/sim_event_intake.py`) merges three sources by
their stamp and hands the dispatcher one instant at a time:

- **the ticks**, grouped into one slice per millisecond of arrival;
- **the simulator's next due time** — a fill, a scheduled modify or cancel — asked for again after
  every instant, so an order sent in one comes due in time; it is an instant of its own between two
  slices, or joins the slice on its stamp. An order never comes due in the instant it was sent in:
  its latency is at least a millisecond, which the configuration enforces (*An order's latency* in
  the [Configuration Cascade Guide](../config_cascade_guide.md), with what other backtesters do).
  A due time before the instant already handed over can therefore only mean inconsistent due
  times: it resolves at once, late, and the scenario's log carries an error, so the run ends as
  finished with errors;
- **a heartbeat logic's timers**, every interval inside a quiet stretch no longer than the gap
  threshold (#208), none across a longer one and none without opting in.

The intake ends with the last slice: nothing past the last tick comes due in the run, and what is
still in flight then is the session end's to settle. It compares the stamps directly rather than
through a heap, which cost about 1.5 µs a tick where every tick is its own instant (measured with
`python/experiments/cost_probes/probe_sim_intake_cost.py`); the AutoTrader's intake does the same.

**The ordering stamp** is a tick's recorded arrival (`collected_msc`), and its venue stamp where its
file recorded none the run can trust. Data preparation clears the arrival of every file the import
marked `arrival_time_reconstructed`, `no_arrival_time` or `arrival_time_offset` — a reconstructed
arrival would make a whole second one slice, and an offset one would shift the run by seconds. Where
two files meet with the later one's first arrival before the earlier one's last, the tick joins the
running instant: like a live session's, the clock only moves forward. Bars still form by the venue's
time.

**Inside an instant** the simulator works in three steps the dispatcher calls in phase order: a
modify or cancel due by then applies before the first tick, so a cancelled order does not fill in
this instant (`resolve_scheduled`); each tick of the slice moves the market and checks the resting
orders and the levels positions carry, so a stop inside a sweep fires on the tick that reached it
(`apply_tick`); and what the latency queue has due fills once, at the prices of the last tick
(`resolve_due`). An order's due time counts from the instant it was sent in — a timer's too, not
the last tick's.

**The algo computes once per slice**, on its last tick — the most recent state, as a live algo meets
it after a long computation — when any tick of the slice is unclipped. The workers count the slice's
unclipped ticks as the ticks they processed, so `ticks_processed` and the fresh and stale figures
still add up to ticks; `algo_passes` counts the computations and `ghost_passes` the timers, and a
figure per pass divides by passes.

**What it changes in a run's results**, on purpose, and every difference traces to one of five
sources: a fill at its due time instead of the next tick; a slice of several ticks computed once; the
phase order inside an instant; the due time counted from the instant; and the clock on the arrival
axis, which moves the record's times by the feed's delay.

## The AutoTrader's timeline (#461)

A session waits for the next thing that falls due instead of polling the tick queue every
heartbeat. Its intake (`loop/intake/live_event_intake.py`) blocks on the tick queue until the
earliest timer is due, is woken when something arrives, merges two lanes and the session's timers
by their stamp, and hands the dispatcher one instant at a time:

- **the market-data lane** — the tick queue, read whole on every pass and never bounded, so a slow
  loop never blocks the WebSocket's thread. Each item on it is one arrival, never split: a venue
  message's trades live, one arrival millisecond in a replay. Live, a backlog the loop fell behind
  on is one slice (*backlog coalescing*): the broker path and the bars see every tick, the algo
  computes once, on the newest — as LEAN builds a live slice from every point that has arrived —
  and the ticks of every arrival before the newest are the clipped ones. A replay's queue fills
  because it runs faster than real time, not because the loop is late, so each of its arrivals is
  a slice of its own, as in a backtest, and its slices never depend on the machine's speed or on
  where a read of the queue falls;
- **the execution lane** — the venue's answers, which stay in the executor's inbox and are read
  only on the loop's thread. The request processor notes each answer's receipt and puts a wake
  marker (`LoopWake`) on the tick queue, so an answer resolves on arrival rather than at the next
  tick or heartbeat, and is never dropped or merged; its resolution joins the first instant at or
  after its receipt. The one exception is a stopping session waiting for its last orders, which
  reads them a heartbeat apart;
- **the timers** (`TimerKind`) — `HEARTBEAT`, one `execution.heartbeat_interval_ms` after the last
  instant with ticks or the last heartbeat, for the executor's heartbeat, the staleness check, the
  ghost pass and the display pulse: it falls due only after a quiet interval, so never inside a
  burst, and neither a venue's answer nor another timer starts it again — answers coming faster
  than the interval would otherwise hold off the staleness check and the ghost pass for as long as
  they come. `RECONCILE`
  after `reconciliation.min_interval_seconds`, `PERSISTENCE` after
  `state_persistence.save_interval_seconds` and `STATE_FRAME_SLOT` at the next period of
  `state_frames.cadence_timeframe` are armed by the runner and armed again after each run; a
  backlog is split at their due time, so the cadence work runs at its time while the market is
  fast. In a replay a timer falls due only once the replay has reached its due time: a replay the
  source throttles runs behind the wall clock, and a timer fired on its deadline would move the
  clock, and a day boundary, past ticks not yet put. The tick-count halves of the reconcile and
  persistence gates stay in the runner, checked after an instant with ticks.

**The stamp** of every instant lies on the receipt axis — the wall clock at arrival live, the
collector's recorded arrival in a replay — and never goes back: an arrival read later and stamped
earlier, which is the two threads' order and not the market's, joins the running instant and is
counted, not refused. A slice stands at the latest receipt among its ticks. A timer is never
stamped before its due time: one that splits a backlog stands at its due time, the heartbeat at the
moment it is read, so a quiet stretch follows the wall clock live and never drifts behind it. A
heartbeat read before its interval is up on the axis — a replay whose source runs behind the loop —
is due at the reading and stands at it — at the last instant where the reading lies behind that —
rather than moving the clock an interval ahead of the replay at every heartbeat. What arrives between two ticks of a replay — a venue's
answer, a timer — is stamped at the loop's own position, the last instant plus the time since: a
replay's own time is the front of its queue, and a loop running behind it would otherwise stamp past
ticks still waiting. Waits and a slice's lag are measured on the monotonic clock.

**After an instant** the runner writes the state frame and the indicator series only when the algo
ran, as a backtest writes nothing on an instant without one, then checks for the session end; a
timer writes the cadence frame through the recorder's own gate, unless a hook ended the session in
that instant's delivery. The clipping monitor counts every
tick of a slice.

**Where the AutoTrader's work lives** (`python/framework/autotrader/event_loop/`). The event loop,
`AutotraderEventLoop`, holds the loop itself: `run()`, the timeline, what follows an instant and
the session's end. The rest is in units of its own, which share the loop's running state
(`AutotraderLoopState` — the tick counts, the tick span, the last tick the algo ran on):

| Unit | What it does |
|---|---|
| `AutotraderPassHooks` | the session's steps the dispatcher calls inside a pass — signals, the market and the account after a tick, the safety layer after the decision, what a decision sent |
| `AutotraderMarketDataWatch` | the market-data staleness contract and its episodes (#436, #451) |
| `AutotraderCadenceWork` | the reconcile, the algo state's save and the carry-over, each behind its tick gate and its timer |
| `AutotraderTradingDays` | what a booking period's seal sets off — the safety day's roll, the ledger, the streams' day records, the log rotation, the operator's day-close message |
| `AutotraderStopWait` | a normal stop's wait for the orders on their way (#630) |

**What the intake counts** (`LiveIntakeStats`): the instants, the coalesced slices, the largest
slice and the longest-waiting one, the reordered stamps, the answers on the execution lane and the timers
fired. Live, lateness is a measurement and never an input.

**What it changes in a session's results**, on purpose, and every difference traces to one of four
sources: the backtest's phase order; a venue's answer resolving on arrival; a backlog computed
once; and the clock on the receipt axis, which moves the record's times by the feed's delay, the
way #460 moved a backtest's.

## What it costs

The loop core runs for every tick of a backtest, so its cost is budgeted: at most 1.0 µs a tick,
refused above 1.5 µs. A throughput tolerance of 20 % cannot see that, so two instruments do:

- the cost probes, which time the pass in its variants with stand-in collaborators
  (`python/experiments/cost_probes/probe_loop_dispatch_cost.py`) and what the backtest's intake
  adds to it (`python/experiments/cost_probes/probe_sim_intake_cost.py`), each with its results
  dated in its docstring;
- for the backtest's timeline (#460), a comparison with the code before it on the benchmark's
  data, where nearly every tick is its own instant: +1.25 µs a tick for the drive and the
  simulator together, measured 2026-11-13 and dated in the intake probe's docstring;
- the benchmark certificate's `loop_glue_avg_ms`, an INFO figure: what a tick costs outside its
  named steps — the drive, the hand-over and the events, and also the ghost passes, the day
  boundary and the state frame, so it is read as a change between two certificates.
