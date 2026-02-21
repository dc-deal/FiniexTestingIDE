# Simulation vs Live: Tick Flow Comparison

Two execution modes, one strategy layer. The trading strategy (DecisionLogic + Workers) runs identically in both modes — only the tick source and execution backend differ.

For class-level architecture details, see [architecture_execution_layer.md](architecture_execution_layer.md).

---

## Backtesting Tick Flow

The backtesting flow processes a finite list of historical ticks synchronously. All components run in sequence per tick.

**Entry point:** `python/framework/process/process_tick_loop.py` → `execute_tick_loop()`

```
execute_tick_loop(config, prepared_objects)
    │
    for tick in ticks:                              # finite, pre-loaded from data provider
        │
        ├── 1. trade_simulator.on_tick(tick)         # AbstractTradeExecutor
        │       ├── Update prices (bid/ask)
        │       ├── _process_pending_orders()         # LatencySimulator: drain tick-based queue
        │       └── _check_sl_tp_triggers(tick)       # Local price check on open positions
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
- Pending orders resolved by tick counter (deterministic, seeded delay)
- `compute()` and `execute_decision()` are **two separate phases** — compute produces a Decision object, execute_decision acts on it

---

## Live Tick Flow

The live flow processes real-time ticks from a broker connection. The runner is **not yet implemented** — the execution layer (LiveTradeExecutor, LiveOrderTracker) is complete and tested.

**Entry point:** `FiniexAutoTrader` (planned, see `ISSUE_live_autotrader_pipeline.md`)

```
FiniexAutoTrader (not yet implemented)
    │
    while running:                                  # infinite, real-time
        │
        tick = await data_feed.next_tick()           # WebSocket / REST poll
        │
        ├── 1. live_executor.on_tick(tick)           # AbstractTradeExecutor
        │       ├── Update prices (bid/ask)
        │       ├── _process_pending_orders()         # LiveOrderTracker: poll broker for fills
        │       └── _check_sl_tp_triggers(tick)       # Live: broker handles SL/TP server-side
        │
        ├── 2. bar_rendering_controller.process_tick(tick)
        │
        ├── 3. bar_history = bar_rendering_controller.get_all_bar_history(symbol)
        │
        ├── 4. worker_coordinator.process_tick(tick, current_bars, bar_history)
        │       ├── Workers compute indicators
        │       └── decision_logic.compute(tick, worker_results) → Decision
        │
        ├── 5. decision_logic.execute_decision(decision, tick)
        │       └── _execute_decision_impl(decision, tick)
        │           └── trading_api.send_order(...)
        │               └── executor.open_order(request)
        │                   └── adapter.execute_order() → Broker API
        │                   └── order_tracker.submit_order() → tracking
        │
        └── 6. Logging / Monitoring
```

**Key characteristics:**
- Ticks arrive in real-time (WebSocket or REST polling)
- Potentially async (tick arrival is event-driven)
- SL/TP handled server-side by broker (MT5, Kraken) — `_check_sl_tp_triggers` is a no-op in live mode
- Pending orders resolved by broker polling (time-based, not tick-based)
- Same `compute()` → `execute_decision()` two-phase pattern

---

## Side-by-Side Comparison

| Aspect | Backtesting | Live |
|--------|-------------|------|
| **Tick source** | Pre-loaded list (finite) | WebSocket / REST (real-time, infinite) |
| **Loop type** | `for tick in ticks` | `while running` / event-driven |
| **Runner** | `execute_tick_loop()` | `FiniexAutoTrader` (not yet built) |
| **Pending orders** | OrderLatencySimulator (tick-based, seeded delay) | LiveOrderTracker → broker polling |
| **SL/TP check** | `_check_sl_tp_triggers()` local price check | Broker server-side (no local check) |
| **Fill detection** | Tick counter reaches `fill_at_tick` | Broker response via `adapter.check_order_status()` |
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
- Interacts with execution only through DecisionTradingAPI

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
- In live: Shadow state that tracks expected broker state (reconciliation needed)
