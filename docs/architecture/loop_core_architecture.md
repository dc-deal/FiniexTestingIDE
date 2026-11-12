# The Loop Core

Both pipelines run the same strategy over the same kind of events — ticks, timers, fills coming
due — but each tick loop used to decide on its own what runs in which order. That is how the two
drifted apart before (the clipping gate once ran before the bars in one loop and after them in the
other). The loop core gives the order one home: an event model with one timeline, and one unit
that owns the order of every pass, fed by each pipeline's own drive — the backtest's today, the
AutoTrader's next.

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
`seq`. A backtest orders by `ts_event`, a live session by `ts_init` (`EventOrdering.pick` is the
one place that says so), and the other stamp travels as data.

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
keeps its drive and hands each pass to an entry; the backtest does today, the AutoTrader's passes
follow:

```
backtest drive (process_tick_loop)              loop core
  for tick in ticks:
      gap walk  ───────────────────────────▶  dispatch_backtest_heartbeat(ghost_msc, …)
      slice_of_tick(tick) ─────────────────▶  dispatch_backtest_slice(event, ctx, hooks, record)
      state frame · session end · live export     clock → day boundary → broker path → bars
                                                  → equity sample → clipping gate → stress +
                                                  tracker → algo → execute → drain
```

What a pass works with is bundled once per run:

- **`LoopContext`** — the run's collaborators: executor, bar controller, orchestrator, decision
  logic, the decision-event channel, the booking recorder and the profiling timers. A pass reads
  its fields directly, because it runs for every tick.
- **The blocks** (`loop/blocks/`) — the steps both pipelines share: rendering a tick's bars,
  computing the workers and the decision, the ghost pass's decision.
- **The hooks** — the runner's own steps inside a pass, at fixed points
  (`AbstractBacktestPassHooks`). A hook exists only where the runner has work, so a pass makes no
  call that does nothing.
- **`LoopPassRecord`** — what a pass leaves for its drive: the ticks applied, the forming bars, the
  bars the tick closed, the decision and what it sent.

A pass ends with its last engine step. What a runner does after it stays in the runner, in its
order: the state frame, the session-end check, the live export, the timing.

### Today: each runner's own order

The two runners order their passes differently today — where the equity is sampled, how often the
decision events drain around a ghost pass — and the dispatcher keeps each runner's order exactly,
so every result stays as it was. The tests in `tests/framework/loop_pass_order/` pin both orders
step by step. The backtest passes run through the dispatcher; the AutoTrader's follow.

The phase order of an instant takes over with the timeline drivers, which feed whole instants: the
backtest's (#460) and the live session's (#461). Both change results on purpose and are measured
against the record of a real-money run before they are merged.

## What it costs

The loop core runs for every tick of a backtest, so its cost is budgeted: at most 1.0 µs a tick,
refused above 1.5 µs. A throughput tolerance of 20 % cannot see that, so two instruments do:

- the cost probe, which times the pass in its variants with stand-in collaborators
  (`python/experiments/cost_probes/probe_loop_dispatch_cost.py`, its results dated in its
  docstring);
- the benchmark certificate's `loop_glue_avg_ms`, an INFO figure: what a tick costs outside its
  named steps — the drive, the hand-over and the events, and also the ghost passes, the day
  boundary and the state frame, so it is read as a change between two certificates.
