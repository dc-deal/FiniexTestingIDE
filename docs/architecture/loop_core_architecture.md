# The Loop Core

Both pipelines run the same strategy over the same kind of events — ticks, timers, fills coming
due — but each tick loop used to decide on its own what runs in which order. That is how the two
drifted apart before (the clipping gate once ran before the bars in one loop and after them in the
other). The loop core gives the order one home: an event model with one timeline, and one unit
that owns the order of every pass, fed by each pipeline's own drive.

This document covers the event model, the timeline and the dispatcher. Not here: what the executor
does inside a step ([Execution Layer](architecture_execution_layer.md)), the two drives side by side
([Simulation vs AutoTrader Flow](simulation_vs_autotrader_flow.md)), and the live loop's clock and
idle cadence ([Live Execution](live_execution_architecture.md)).

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
backtests, by when a datum became available. `EventOrdering.pick` is the one place that says which
stamp an ordering takes, and the other stamp travels as data.

The timeline (`loop/event_timeline.py`) hands out the earliest stamp first, then by `seq`, and never
one kind before another. Events sharing a stamp form one **instant** and leave together; the seven
phases of an instant (`InstantPhase`) order the work inside it — market data first, so a fill due
in the same millisecond as a tick fills at that tick's price. An event stamped before the last
instant handed over is refused: time handed over never runs back.

Events are dataclasses with slots and are not frozen; nothing writes one after it is made. One is
built for every instant a backtest replays, and the frozen form cost more than the loop core's
whole budget per tick (measured with `python/experiments/cost_probes/probe_loop_dispatch_cost.py`).

## The dispatcher

`loop/loop_event_dispatcher.py` holds the order of a pass as code — one entry per kind of pass,
never a table, because a table hides its conditions and invites reordering at runtime. A runner
keeps its drive and hands each pass to an entry:

```
backtest drive (process_tick_loop)              loop core
  for instant in SimEventIntake(…): ───────▶  dispatch_backtest_instant(events, simulator, ctx,
      state frame · series · session end                                 hooks, record)
      · live export                               clock → day boundary → scheduled modifies and
                                                  cancels → for each tick: broker path, bars,
                                                  equity sample → closed bars → what came due
                                                  → drain → clipping gate → stress + tracker
                                                  → algo, once → execute → drain
                                                  — at a timer: ghost → execute → drain

AutoTrader drive (AutotraderTickLoop.run)
  while running:
      no tick within the heartbeat ────────▶  dispatch_live_timer(now, ctx, hooks)
                                                  clock → day boundary → heartbeat → staleness
                                                  → reconcile → signal inbox → drain → ghost
                                                  → safety override → execute
      slice_of_tick(tick) ─────────────────▶  dispatch_live_slice(event, ctx, hooks, record)
      session end · reconcile · persistence       signal inbox → clock → day boundary → broker
      · clipping monitor · display · frame        path → market status + account value → bars
                                                  → algo → safety → safety override → execute
                                                  → drain
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
  the AutoTrader's tick loop implements itself, because the steps are its own state). A hook
  exists only where the runner has work, so a pass makes no call that does nothing.
- **`LoopPassRecord`** — what a pass leaves for its drive: the ticks applied, the forming bars, the
  bars the tick closed, the decision and what it sent.

A pass ends with its last engine step. What a runner does after it stays in the runner, in its
order: the state frame, the session-end check, the live export, the timing — and in the AutoTrader
the reconcile after a tick, the persistence, the clipping monitor and the display.

### Two orders until the live driver

The backtest runs its instants in the phase order of the event model since #460; the AutoTrader
keeps the order of its own tick loop until its timeline driver (#461). The tests in
`tests/framework/loop_pass_order/` pin both orders step by step. Where the two differ in the order
of the steps they share: the AutoTrader folds in its signal inbox before the clock moves, reads the
market-data status and the account value before the bars where the backtest reads them after,
samples the account again after the algo for its safety layer and may turn a decision into FLAT,
and delivers the decision events only after the algo, where the backtest delivers them before it as
well — so a fill reaches a backtest's hooks one pass earlier than a session's. Beyond that each pass
has steps of its own — the backtest's clipping gate, the AutoTrader's staleness, reconcile and
signal inbox on its idle pass. The diagram above and the pass-order tests hold the full orders.

## The backtest's timeline (#460)

The backtest's drive is a next-event clock: it jumps from one event to the next rather than walking
its tick array one tick at a time. The intake (`loop/sim_event_intake.py`) merges three sources by
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
through the event timeline, because a heap costs about 1.5 µs a tick where every tick is its own
instant (measured with `python/experiments/cost_probes/probe_sim_intake_cost.py`); the timeline
stays the live driver's to weigh.

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
