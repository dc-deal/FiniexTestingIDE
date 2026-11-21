# Simulation vs AutoTrader: Tick Flow Comparison

Two execution modes, one strategy layer. The trading strategy (DecisionLogic + Workers) runs identically in both modes — only the tick source and execution backend differ.

For class-level architecture details, see [architecture_execution_layer.md](architecture_execution_layer.md).
For how the console dashboards get their live data (the throttled telemetry feed), see [live_telemetry_architecture.md](live_telemetry_architecture.md).

---

## Backtesting Tick Flow

The backtesting flow processes a finite list of historical ticks synchronously. The clock jumps
from one event to the next (#460): the ticks, grouped by the millisecond they arrived in; the
simulator's next due time — a fill, a scheduled modify or cancel; and a heartbeat logic's timers.

**Entry point:** `python/framework/process/process_event_loop.py` → `execute_event_loop()`. The
timeline intake (`SimEventIntake`) merges the three sources and hands over one instant at a time;
each instant runs in the loop core (`dispatch_backtest_instant`,
[The Loop Core](loop_core_architecture.md)), in the phases of the event model.

```
execute_event_loop(config, worker_coordinator, trade_simulator, bar_rendering_controller, decision_logic, scenario_logger, ticks)
    │
    for instant in SimEventIntake(ticks, trade_simulator, ...):
        │                                         # a slice (ticks that arrived in one ms), a due time,
        │                                         #   a timer — or several of them at one stamp
        ├── 1. clock → the instant's stamp, once; the trading-day boundary is checked on it
        │
        │   ═══ MARKET DATA (every tick of the slice, clipped or not) ═══
        │
        ├── 2. trade_simulator.resolve_scheduled(stamp)   # modifies and cancels due by now — a due
        │                                                 #   cancel keeps its order from filling here
        ├── 3. for tick in slice:
        │       ├── trade_simulator.apply_tick(tick)      # prices; resting limits and stops; SL/TP
        │       ├── render_bars_for_tick(tick, ...)       # SHARED CORE (#303) — bars see every tick
        │       └── equity sample, booking observation
        │
        │   ═══ BROKER RESOLUTIONS ═══
        │
        ├── 4. trade_simulator.resolve_due(stamp)         # the latency queue, at the last tick's prices
        ├── 5. drain()                                    # fills and cancels reach the hooks first
        │
        ├── if every tick of the slice is clipped: next instant
        │
        │   ═══ ALGO PASS (once per slice, on its last tick) ═══
        │
        ├── 6. execute_algo_path(last tick, ...)          # SHARED CORE (#303)
        │       ├── bar_history retrieval
        │       ├── Workers compute indicators
        │       └── decision_logic.compute_tick(tick, worker_results) → Decision
        │   or, at a timer: process_heartbeat() → compute_heartbeat (a ghost pass)
        │
        ├── 7. decision_logic.execute_decision(decision, tick)
        │       └── trading_api.send_order(...) → executor.open_order(request)
        │           └── LatencySimulator.submit() → PendingOrder, due at the instant + its latency
        ├── 8. drain()
        │
        └── 9. state frame, indicator series, process_live_export(...)

    # Session end (#348, #642) — only when the logic subscribes to decision events
    decision_event_dispatcher.emit_session_end()         # on_session_end; the executor
                                                         #   records what is sent from here on
    trade_simulator.settle_session_end_orders()          # its orders, at the last quote
    decision_event_dispatcher.emit_session_end_orders()  # on_session_end_order_outcome
    trade_simulator.withdraw_unsent_orders()             # what that hook sent, never sent

    # Post-loop cleanup
    trade_simulator.finish_remaining_orders()
    trade_simulator.check_clean_shutdown()

    # Collect results
    → ProcessEventLoopResult (stats, trade_history, order_history, order_events, active_orders, profiling)
```

**Key characteristics:**
- Ticks are pre-loaded (finite list from historical data)
- Synchronous: each step completes before the next starts
- SL/TP triggers checked locally (`_check_sl_tp_triggers`), in this pipeline only: a session holds no level (#625)
- An order fills when it comes due, not at the next tick: its due time is an instant of the
  timeline, between two ticks if need be, at the prices of the last tick before (deterministic,
  seeded delay)
- **Ordered by arrival (#460):** a tick's recorded arrival (`collected_msc`), or its venue stamp
  where its file recorded none it can trust — data preparation clears an arrival that was
  reconstructed, offset or never recorded. The algo sees a tick no earlier than a live session
  would have; bars still form by the venue's time
- **One algo pass per slice:** ticks that arrived in one millisecond reach the broker path and the
  bars one by one, so a stop inside a sweep fires on its tick, and the algo computes once, on the
  most recent state. `ticks_processed` still counts ticks; `algo_passes` counts the computations
- `compute_tick()` and `execute_decision()` are **two separate phases** — compute produces a Decision object, execute_decision acts on it
- **Tick processing budget:** When active, ticks are flagged as `is_clipped` during data
  preparation. The broker path (steps 2 to 4) sees every tick — pending order fills, SL/TP
  triggers, and limit/stop monitoring operate on the full market data stream. The algo computes on
  a slice when any of its ticks is unclipped, and skips a slice whose every tick is clipped. When
  budget is disabled (default), `is_clipped` is always `False`.
- **Ghost-pass in simulated time (#360, opt-in):** for a decision that opts in via
  `wants_heartbeat()`, the intake makes a timer every `heartbeat_interval_ms` in the quiet stretch
  between two slices. At a timer the clock moves to it, what came due resolves, and
  `process_heartbeat()` → `execute_decision(tick=None)` runs. This gives the **same relative
  reaction point** as the AutoTrader ghost-pass (sim/live parity). It is **hard-gated**: a
  non-opt-in decision gets no timer at all. A **correctness gate** makes no timer across a gap
  longer than `inter_tick_gap_threshold_s` (#208) — across a data/weekend gap the market says
  nothing. The clock is the timeline's (simulated time, deterministic) and never freezes to the
  last tick.

---

## AutoTrader Tick Flow

The AutoTrader flow processes the ticks of its tick source — in a live-adapter session, real-time
ticks from a broker connection. The runner is `AutotraderEventLoop` — implemented and validated
against real Kraken Spot. Ticks are pulled from a thread-safe queue fed
by a TickSource thread (KrakenTickSource — Kraken WebSocket v2); the loop is synchronous on the main
thread.

**Entry point:** `python/framework/autotrader/event_loop/autotrader_event_loop.py` → `AutotraderEventLoop.run()`

```
AutotraderEventLoop.run()
    │
    intake = LiveEventIntake(…)                     # #461: two lanes and the session's timers
    while running:                                  # infinite, real-time
        │
        events = intake.next_instant()              # waits for the next timer, woken by a tick or an answer
        │   slice of the ticks that arrived (live: a backlog is ONE slice) · the venue's answers
        │   · HEARTBEAT / RECONCILE / PERSISTENCE / STATE_FRAME_SLOT timers — one stamp
        │
        │   dispatch_live_instant(events, executor, …) — the loop core; the full order in
        │   loop_core_architecture.md:
        │
        ├── 0. clock (the instant's stamp) · day boundary
        │
        ├── 1. for each tick: executor.apply_tick(tick)   # prices, market state
        │                     render_bars_for_tick(...)    # SHARED CORE (#303)
        │                     market status + account value
        │
        ├── 2. signal inbox
        │
        ├── 3. executor.resolve_due(stamp)            # after a tick: _process_pending_orders()
        │       │                                     #   (incl. the synchronous MARKET status read)
        │       │                                     # otherwise: heartbeat() — drain, timeouts, re-poll
        │       └── HEARTBEAT: staleness · RECONCILE: reconcile (#151)
        │
        ├── 4. drain #348 decision events             # fills/cancels delivered to algo hooks
        │
        ├── 5. execute_algo_path(newest tick, ...)    # SHARED CORE (#303), once per slice
        │       ├── bar_history retrieval
        │       ├── Workers compute indicators
        │       └── decision_logic.compute_tick(tick, worker_results) → Decision
        │   or, at a HEARTBEAT: process_heartbeat()   # decision GHOST-PASS (tick=None, opt-in)
        │
        ├── 6. safety check (circuit breaker)         # blocked → override decision to FLAT
        │
        ├── 7. decision_logic.execute_decision(decision, tick)
        │       └── _execute_decision_impl(decision, tick)
        │           └── trading_api.send_order(...)
        │               └── executor.open_order(request)
        │                   └── adapter.execute_order() → Broker API
        │                   └── order_tracker.submit_order() → tracking
        │
        ├── 8. drain #348 decision events
        │
        │   then, in run():
        └── 9. reconcile and persistence by tick count, carry-over, clipping monitor, display,
               state frame and indicator series (only where the algo ran), session-end check
```

**Key characteristics:**
- Ticks arrive in real-time via WebSocket, buffered through a thread-safe queue
- A session holds no stop or target (#625): the start refuses a declared one its venue's API does not
  hold, and a logic keeps its own (`OwnExitLevels`). The level check on a position is the simulation's
  alone. Contract: `docs/architecture/venue_order_features.md`
- Pending orders resolved by broker polling today (#320 cadence); WebSocket push is the V1.4 primary (#331)
- Fills reach the algo via the #348 Decision Event Channel on arrival: the venue's answer wakes the loop and resolves in the next instant (#461) — except while a stopping session waits for its last orders, which reads them a heartbeat apart
- The Reconciler (#151) runs as a separate trust layer (ALERT_ONLY) — it verifies broker truth, it does not learn fills
- Idle handling (#360): the heartbeat falls due one `heartbeat_interval_ms` (default 500 ms) after
  the last instant with ticks or the last heartbeat — the single main-loop consumer advances the
  injected clock, re-polls active orders and runs a side-effect-free decision **ghost-pass** (`tick=None`, cached worker
  results, opt-in via `wants_heartbeat()`). The reconciliation and the persistence are timers of
  their own (#461). No second thread, no synthetic market tick, no tick-state mutation.
- Canonical clock (#360, #461): `get_current_time()` is the instant's stamp on the receipt axis —
  the wall clock at arrival live, the recorded arrival in a mock session's replay (#616); sim: the
  recorded arrival too. It never goes back and never freezes to the last tick.
- Same `compute_tick()` → `execute_decision()` two-phase pattern

---

## Shared Blocks and the Loop Core (#303, #458)

The identical algo-pipeline steps live in ONE implementation each — the loop core's blocks under
`python/framework/loop/blocks/` — called by both runners, so ordering divergence (the #293 bug
class) is impossible by construction. Both runners hand each pass to the loop core's dispatcher,
which owns its order; see [The Loop Core](loop_core_architecture.md):

| Function | Steps | Called for |
|---|---|---|
| `render_bars_for_tick` | 2 — bar aggregation | every tick, BEFORE any gate |
| `execute_algo_path` | bar history + worker/decision compute | a backtest slice with an unclipped tick (once, on its last tick); a non-clipped live tick |
| `run_ghost_pass` | heartbeat decision compute (#360) | idle heartbeats (opt-in algos) |

Outside the shared blocks, each runner's own steps sit in its own dispatcher entry, in the one
phase order of the event model. The backtest's `dispatch_backtest_instant` holds the clipping
gate, the per-step profiling and the error wrap around `execute_decision`. The AutoTrader's
`dispatch_live_instant` (#461) holds the safety override and has no clipping gate and no
profiling; `dispatch_live_order_wait` is one pass of a stop's wait. Both keep the #348 event-drain
boundary and set the clock to the instant's stamp on the receipt axis. The reconcile and
persistence gates, the clipping monitor, the display and the state frame follow a pass in the
AutoTrader's event loop.

## Side-by-Side Comparison

| Aspect | Backtesting | Live-adapter session |
|--------|-------------|------|
| **Tick source** | Pre-loaded list (finite) | WebSocket / REST (real-time, infinite) |
| **Loop type** | `for instant in SimEventIntake(...)` — next event: slice, due time, timer | `while running` / event-driven |
| **Runner** | `execute_event_loop()` | `AutotraderEventLoop.run()` |
| **Pending orders** | OrderLatencySimulator (ms-timestamp, seeded delay) | LiveRequestProcessor → broker polling |
| **SL/TP check** | `_check_sl_tp_triggers()` local price check of the levels a position carries | None: a session holds no level (#625). A logic's own exit (`OwnExitLevels`) closes through `close_position()`, so it lands at the broker's next price instead of at the level |
| **Fill detection** | An instant at `broker_fill_msc`, on the arrival axis (#460) | Broker response via polling (#320); WebSocket push primary in V1.4 (#331) |
| **Fill price** | Bid/ask of the last tick at or before the due time | Broker's actual execution price |
| **Latency model** | Seeded random (deterministic, reproducible) | Real network latency |
| **Error source** | Stress test injection (configurable) | Real broker errors / timeouts |
| **Strategy layer** | Identical | Identical |
| **Portfolio logic** | Identical (`_fill_open_order`, `_fill_close_order`) | Identical |

**What stays the same:** bar rendering, workers, decision compute and decision execute are the same blocks in both modes. The strategy never knows which mode it runs in.

**What differs:** how pending orders are resolved, the tick source, and — until the live driver takes the phase order too (#461) — the order inside a pass: the backtest computes once per slice and delivers the decision events before the algo as well as after it. Everything else is shared infrastructure.

---

## Event-Driven Hybrid Model

The architecture follows an **Event-Driven Hybrid** pattern with swappable components at each layer:

```
EventSource (swappable)
    │
    │  ticks
    ▼
Strategy (identical in both modes)
    │
    │  orders
    ▼
ExecutionHandler (swappable)
    │                  │
    │  pending         │  fill trigger
    ▼                  ▼
PendingOrderManager    Fill Processing
(swappable)            (shared)
    │
    │  confirmed fills
    ▼
Portfolio (shared)
```

**EventSource:**
- Simulation: TickDataProvider reads historical CSV/binary tick data
- Live-adapter session: WebSocket connection to broker delivers real-time ticks

**Strategy:**
- DecisionLogic + Workers — completely unchanged between modes
- Receives ticks, produces trading decisions
- Interacts with execution only through DecisionTradingApi

**ExecutionHandler:**
- Simulation: TradeSimulator with OrderLatencySimulator
- AutoTrader: LiveTradeExecutor with LiveRequestProcessor

**PendingOrderManager:**
- Simulation: OrderLatencySimulator (tick-based fill detection)
- AutoTrader: LiveRequestProcessor (broker-response fill detection)
- Both inherit from AbstractPendingOrderManager (shared storage/query)

**Fill Processing:**
- AbstractTradeExecutor._fill_open_order() / _fill_close_order()
- Shared by all modes — no duplication

**Portfolio:**
- PortfolioManager — shared, single source of truth
- In simulation: IS the truth (no external state to reconcile)
- In the AutoTrader: Shadow state that tracks expected broker state — verified by the Reconciler (#151, ALERT_ONLY); correction (#349) lands in V1.4
