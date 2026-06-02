# Simulation vs Live: Tick Flow Comparison

Two execution modes, one strategy layer. The trading strategy (DecisionLogic + Workers) runs identically in both modes — only the tick source and execution backend differ.

For class-level architecture details, see [architecture_execution_layer.md](architecture_execution_layer.md).

---

## Backtesting Tick Flow

The backtesting flow processes a finite list of historical ticks synchronously. All components run in sequence per tick.

**Entry point:** `python/framework/process/process_tick_loop.py` → `execute_tick_loop()`

```
execute_tick_loop(config, worker_coordinator, trade_simulator, bar_rendering_controller, decision_logic, scenario_logger, ticks)
    │
    for tick in ticks:                              # finite, pre-loaded from data provider
        │
        │   ═══ BROKER PATH (all ticks) ═══
        │
        ├── 1. trade_simulator.on_tick(tick)         # AbstractTradeExecutor
        │       ├── Update prices (bid/ask)
        │       ├── _process_pending_orders()         # LatencySimulator: drain tick-based queue
        │       └── _check_sl_tp_triggers(tick)       # Local price check on open positions
        │
        ├── if tick.is_clipped: continue             # Clipping gate (budget active only)
        │
        │   ═══ ALGO PATH (non-clipped ticks only) ═══
        │
        ├── 2. bar_rendering_controller.process_tick(tick)
        │       └── Aggregate tick into OHLC bars
        │
        ├── 3. bar_history = bar_rendering_controller.get_all_bar_history(symbol)
        │
        ├── 4. worker_coordinator.process_tick(tick, current_bars, bar_history)
        │       ├── Workers compute indicators
        │       └── decision_logic.compute(tick, worker_results) → Decision
        │           └── Pure analysis: BUY / SELL / HOLD + metadata (no side effects)
        │
        ├── 5. decision_logic.execute_decision(decision, tick)
        │       └── _execute_decision_impl(decision, tick)          # abstract, per strategy
        │           └── trading_api.send_order(symbol, order_type, direction, ...)
        │               └── executor.open_order(request)
        │                   └── LatencySimulator.submit() → PendingOrder
        │
        └── 6. process_live_export(...)              # Dashboard updates (time-based)

    # Post-loop cleanup
    trade_simulator.close_all_remaining_orders()
    trade_simulator.check_clean_shutdown()

    # Collect results
    → ProcessTickLoopResult (stats, trade_history, order_history, pending_stats, profiling)
```

**Key characteristics:**
- Ticks are pre-loaded (finite list from historical data)
- Synchronous: each step completes before the next starts
- SL/TP triggers checked locally (`_check_sl_tp_triggers`)
- Pending orders resolved by ms-timestamp comparison (deterministic, seeded delay)
- `compute()` and `execute_decision()` are **two separate phases** — compute produces a Decision object, execute_decision acts on it
- **Tick processing budget:** When active, ticks are flagged as `is_clipped` during data preparation. The broker path (step 1) sees every tick — pending order fills, SL/TP triggers, and limit/stop monitoring operate on the full market data stream. The algo path (steps 2-6) skips clipped ticks via `continue`. When budget is disabled (default), `is_clipped` is always `False` and all ticks pass through both paths.

---

## Live Tick Flow

The live flow processes real-time ticks from a broker connection. The runner is `AutotraderTickLoop` — implemented and validated against live Kraken Spot. Ticks are pulled from a thread-safe queue fed by a TickSource thread (KrakenTickSource — Kraken WebSocket v2); the loop is synchronous on the main thread.

**Entry point:** `python/framework/autotrader/autotrader_tick_loop.py` → `AutotraderTickLoop.run()`

```
AutotraderTickLoop.run()
    │
    while running:                                  # infinite, real-time
        │
        tick = tick_queue.get(timeout=1.0)           # fed by TickSource thread (WebSocket)
        │   └── queue.Empty (idle) → executor.heartbeat()   # drain async responses, no tick state
        │                          → drain #348 events       # idle-time fills (poll/push)
        │                          → push display pulse       # "💓 Ns since last tick"
        │
        ├── 1. executor.on_tick(tick)                # AbstractTradeExecutor
        │       ├── Update prices (bid/ask)
        │       ├── _process_pending_orders()         # LiveOrderTracker: poll broker for fills
        │       └── _check_sl_tp_triggers(tick)       # Live: broker handles SL/TP server-side (no-op)
        │
        ├── 2. bar_controller.process_tick(tick)
        │
        ├── 3. bar_history = bar_controller.get_all_bar_history(symbol)
        │
        ├── 4. worker_orchestrator.process_tick(tick, current_bars, bar_history)
        │       ├── Workers compute indicators
        │       └── decision_logic.compute(tick, worker_results) → Decision
        │
        ├── 5. safety check (circuit breaker)         # blocked → override decision to FLAT
        │
        ├── 6. decision_logic.execute_decision(decision, tick)
        │       └── _execute_decision_impl(decision, tick)
        │           └── trading_api.send_order(...)
        │               └── executor.open_order(request)
        │                   └── adapter.execute_order() → Broker API
        │                   └── order_tracker.submit_order() → tracking
        │
        ├── 7. drain #348 decision events             # fills/cancels delivered to algo hooks
        │
        ├── 8. reconcile (#151, hybrid cadence)       # broker truth-pull, ALERT_ONLY
        │
        └── 9. clipping monitor + display stats
```

**Key characteristics:**
- Ticks arrive in real-time via WebSocket, buffered through a thread-safe queue
- SL/TP handled server-side by broker (MT5, Kraken) — `_check_sl_tp_triggers` is a no-op in live mode
- Pending orders resolved by broker polling today (#320 cadence); WebSocket push is the V1.4 primary (#331)
- Fills on the fast path reach the algo immediately via the #348 Decision Event Channel — drained each tick AND during idle heartbeats
- The Reconciler (#151) runs as a separate trust layer (ALERT_ONLY) — it verifies broker truth, it does not learn fills
- Idle handling: a tick gap triggers `heartbeat()` + a display pulse frame
- Same `compute()` → `execute_decision()` two-phase pattern

---

## Side-by-Side Comparison

| Aspect | Backtesting | Live |
|--------|-------------|------|
| **Tick source** | Pre-loaded list (finite) | WebSocket / REST (real-time, infinite) |
| **Loop type** | `for tick in ticks` | `while running` / event-driven |
| **Runner** | `execute_tick_loop()` | `AutotraderTickLoop.run()` |
| **Pending orders** | OrderLatencySimulator (ms-timestamp, seeded delay) | LiveOrderTracker → broker polling |
| **SL/TP check** | `_check_sl_tp_triggers()` local price check | Broker server-side (no local check) |
| **Fill detection** | Tick timestamp `collected_msc` >= `broker_fill_msc` | Broker response via polling (#320); WebSocket push primary in V1.4 (#331) |
| **Fill price** | Current tick bid/ask at fill time | Broker's actual execution price |
| **Latency model** | Seeded random (deterministic, reproducible) | Real network latency |
| **Error source** | Stress test injection (configurable) | Real broker errors / timeouts |
| **Strategy layer** | Identical | Identical |
| **Portfolio logic** | Identical (`_fill_open_order`, `_fill_close_order`) | Identical |

**What stays the same:** Steps 2-5 (bar rendering, workers, decision compute, decision execute) are completely unchanged. The strategy never knows which mode it runs in.

**What differs:** Step 1 (how pending orders are processed) and the tick source. Everything else is shared infrastructure.

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
- Live: WebSocket connection to broker delivers real-time ticks

**Strategy:**
- DecisionLogic + Workers — completely unchanged between modes
- Receives ticks, produces trading decisions
- Interacts with execution only through DecisionTradingApi

**ExecutionHandler:**
- Simulation: TradeSimulator with OrderLatencySimulator
- Live: LiveTradeExecutor with LiveOrderTracker

**PendingOrderManager:**
- Simulation: OrderLatencySimulator (tick-based fill detection)
- Live: LiveOrderTracker (broker-response fill detection)
- Both inherit from AbstractPendingOrderManager (shared storage/query)

**Fill Processing:**
- AbstractTradeExecutor._fill_open_order() / _fill_close_order()
- Shared by all modes — no duplication

**Portfolio:**
- PortfolioManager — shared, single source of truth
- In simulation: IS the truth (no external state to reconcile)
- In live: Shadow state that tracks expected broker state — verified by the Reconciler (#151, ALERT_ONLY); correction (#349) lands in V1.4
