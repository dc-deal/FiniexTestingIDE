# Execution Layer Architecture: Simulation & Live Trading

## Overview

This document describes the architecture of the trade execution layer — the system that sits between trading strategy (DecisionLogic) and the market. It explains the design principles, the Simulation/Live hybrid approach, and the reasoning behind each architectural decision.

The core insight: **Backtesting and live trading share the same portfolio logic.** The only difference is *how* orders reach the market and *how* fills are confirmed. Everything else — portfolio tracking, fee calculations, P&L accounting, margin checks — is identical.

> **Tick flow comparison (Backtesting vs Live):** see [simulation_vs_live_flow.md](simulation_vs_live_flow.md)
> **Live execution details (LiveTradeExecutor, broker polling, LiveOrderTracker):** see [live_execution_architecture.md](live_execution_architecture.md)

---

## The Problem: Two Execution Modes, One Strategy

A trading strategy should not know (or care) whether it's running in a backtest or live. The decision "buy 0.1 lots EURUSD" is the same regardless of execution mode. But the *execution* is fundamentally different:

| Aspect | Simulation | Live |
|--------|-----------|------|
| Order submission | Internal queue | Broker API call |
| Fill confirmation | Deterministic delay (ticks) | Broker response (async) |
| Price source | Historical tick data | WebSocket feed |
| Latency | Simulated (seeded) | Real network latency |
| Failure modes | Seeded errors (stress testing) | Real network/broker errors |

The naive approach — two completely separate systems — leads to code duplication, behavior divergence, and the constant risk that "it worked in backtest but not live."

---

## The Hybrid Architecture

The solution is a **shared-core architecture** where common logic lives in base classes, and mode-specific behavior is isolated in subclasses.

### Full Layer Diagram

```
┌─────────────────────────────────────────────────┐
│                DecisionLogic                     │
│  (SimpleConsensus, AggressiveTrend, etc.)        │
│  Knows NOTHING about execution internals.        │
└──────────────────┬──────────────────────────────┘
                   │  calls
┌──────────────────▼──────────────────────────────┐
│             DecisionTradingAPI                    │
│  Public API surface. Validates order types.       │
│  Routes to executor. Hides execution mode.        │
└──────────────────┬──────────────────────────────┘
                   │  delegates to
┌──────────────────▼──────────────────────────────┐
│          AbstractTradeExecutor                    │
│  SHARED: Fill processing, portfolio,              │
│  fee calculations, statistics, price state        │
│                                                   │
│  ABSTRACT: Order submission, pending order         │
│  processing, fill detection                       │
├──────────────────┬──────────────────────────────┤
│                  │                               │
│  TradeSimulator  │    LiveTradeExecutor           │
│  (Simulation)    │    (Live)                      │
│                  │                               │
│  has-a:          │    has-a:                      │
│  OrderLatency    │    LiveOrderTracker            │
│  Simulator       │                               │
└──────────────────┴──────────────────────────────┘

            Both "has-a" inherit from:

┌─────────────────────────────────────────────────┐
│       AbstractPendingOrderManager                │
│  SHARED: Storage, query, has_pending,            │
│  is_pending_close, clear                         │
├──────────────────┬──────────────────────────────┤
│                  │                               │
│  OrderLatency    │    LiveOrderTracker            │
│  Simulator       │                               │
│                  │                               │
│  - SeededDelay   │    - Broker ref tracking       │
│    Generator     │    - mark_filled/rejected      │
│  - process_tick  │    - check_timeouts            │
│  - tick-based    │    - time-based                │
│    fill detect   │      fill detect              │
└──────────────────┴──────────────────────────────┘
```

### The Key Principle: "What Happens" vs "How It Gets There"

The abstract base class answers **what happens when an order is filled**: portfolio gets a new position, fees are calculated, margin is checked, statistics are updated. This is the same whether the fill came from a latency simulator or a real broker.

The subclasses answer **how the order gets to the market and how we learn it was filled**: TradeSimulator uses a deterministic queue with seeded delays. LiveTradeExecutor calls a broker adapter and polls for confirmations.

---

## Two Paths That Converge

This is the central architectural principle: **Simulation and Live follow the same path, with different triggers.**

### Simulation Path

```
1. DecisionLogic calls send_order()
2. TradeSimulator.open_order()
   → Creates PendingOrder with fill_at_tick
   → Stores in OrderLatencySimulator (inherited storage)
3. Each tick: on_tick() → _process_pending_orders()
   → OrderLatencySimulator.process_tick() checks tick counter
   → Returns orders whose delay has elapsed
4. For each filled order:
   → _fill_open_order(pending_order)        ← SHARED (AbstractTradeExecutor)
   → Portfolio updated, fees calculated     ← SHARED
```

### Live Path

> Moved to [live_execution_architecture.md](live_execution_architecture.md) — includes full broker polling flow with broker_ref lifecycle.

Steps 1, 3 (structure), and 4 are **identical** between simulation and live. Only the trigger differs: tick counter vs broker response.

### Error Handling: Same Code, Both Modes

Error handling is **not a live-only feature**. It belongs in AbstractTradeExecutor because both modes need it. The simulator needs it for stress testing, live needs it for reality. Same code, same paths.

**Two error sources, one handling path:**

1. **Structural rejections** (AbstractTradeExecutor): Margin check fails during `_fill_open_order()` → rejection stored in `_order_history`, counters updated. This code runs in both modes — identical for simulation and live.

2. **Stress test rejections** (TradeSimulator): `_stress_test_should_reject()` intercepts orders before they reach fill processing → simulates broker-level errors (BROKER_ERROR). Controlled by module constants `STRESS_TEST_REJECTION_ENABLED` and `STRESS_TEST_REJECT_EVERY_N`.

**Simulator with Stress Testing (implemented):**
```
1. open_order() → PendingOrder in OrderLatencySimulator
2. Latency delay elapses → order ready for fill
3. _stress_test_should_reject() → "Every 3rd order → BROKER_ERROR"
4. Rejection stored in _order_history, counter incremented
5. DecisionLogic never sees the rejection directly — but sees
   no position created, uses has_pending_orders() to detect stalled state
```

**Simulator with Margin Rejection (implemented):**
```
1. open_order() → PendingOrder in OrderLatencySimulator
2. Latency delay elapses → _fill_open_order() called
3. Margin check: required > free margin → INSUFFICIENT_MARGIN
4. Rejection stored in _order_history, order never reaches portfolio
```

**Live (implemented):** See [live_execution_architecture.md](live_execution_architecture.md) — same handling logic as simulation stress test, triggered by real broker errors/timeouts instead of seeded injection.

The advantage: You can test your error-handling logic in the simulator before going live. "Reject every 3rd trade" validates your algorithm handles:
- Rejections correctly (no duplicate order submissions)
- Timeouts cleanly (no ghost positions in the pending cache)
- Recovery to a consistent state after failures
- Correct margin calculations despite failed orders

### Reporting Integration

Rejection data flows through the full reporting pipeline:

1. **Subprocess bridge**: `order_history` collected from `trade_simulator.get_order_history()`, serialized in `ProcessTickLoopResult`
2. **Trade History Summary**: Per-scenario rejection table (order ID, reason, message) + aggregated rejection breakdown by reason
3. **Executive Summary**: Order execution stats with rejected count and execution rate
4. **Portfolio Grid Boxes**: Conditional display — shows rejection count when rejections exist

### Pending Order Statistics

Every pending order that leaves the queue (filled, rejected, timed out, or force-closed) is recorded via `AbstractPendingOrderManager.record_outcome()`. Statistics are aggregated at the manager level — no individual records are stored for normal outcomes.

**Data flow:**

1. **Executor calls `record_outcome()`** after each pending order resolves — TradeSimulator for tick-based fills, LiveTradeExecutor for broker responses
2. **`PendingOrderStats`** aggregates running min/max/avg latency using internal counters (`_latency_ticks_sum`, `_latency_count`). No individual fill records stored.
3. **Anomaly detection**: Only `FORCE_CLOSED` and `TIMED_OUT` outcomes produce individual `PendingOrderRecord` entries (stored in `anomaly_orders` list)
4. **Subprocess bridge**: `pending_stats` collected from `trade_simulator.get_pending_stats()`, serialized as separate field in `ProcessTickLoopResult`
5. **Aggregation**: `PortfolioAggregator._aggregate_pending_stats()` combines stats across scenarios using weighted averages

**Outcome types** (`PendingOrderOutcome` enum in `latency_simulator_types.py`):

| Outcome | Source | Individual Record | Latency Unit |
|---------|--------|-------------------|--------------|
| `FILLED` | Normal fill after delay | No (aggregated only) | ticks (sim) / ms (live) |
| `REJECTED` | Stress test or broker rejection | No (aggregated only) | ticks (sim) / ms (live) |
| `TIMED_OUT` | Broker timeout (live only) | Yes (`anomaly_orders`) | ms |
| `FORCE_CLOSED` | `clear_pending()` for genuine stuck-in-pipeline orders at scenario end | Yes (`anomaly_orders`, with `reason`) | ticks (sim) / ms (live) |

**Display locations:**

- **Portfolio Grid Boxes**: Green latency line `"Latency: avg 4.7t (3-8)"`, yellow `"X forced"` / `"X timeout"` if anomalies
- **Aggregated Portfolio (ORDER EXECUTION)**: Resolved breakdown with filled/rejected/timed_out/force-closed counts + latency stats
- **Executive Summary**: Green latency line per scenario, yellow `"X force-closed"` / `"X timed out"` breakdown

**End-of-scenario cleanup** (`close_all_remaining_orders`):

Open positions are closed via synthetic PendingOrders that bypass the latency pipeline entirely — no pending created, no statistics impact. This is an internal cleanup, not an algo action. After direct-filling, `clear_pending()` catches any genuine stuck-in-pipeline orders (e.g. algo submitted an order right before scenario ended). Only these real anomalies are recorded as `FORCE_CLOSED` with a `reason` field (e.g. `"scenario_end"`, `"manual_abort"`).

### History Retention Limits

In-memory history collections use configurable limits to prevent unbounded growth during long-running scenarios. Configured via `app_config.json` → `"history"` section:

```json
{
    "history": {
        "bar_max_history": 1000,
        "order_history_max": 10000,
        "trade_history_max": 5000
    }
}
```

| Collection | Location | Default | Implementation |
|-----------|----------|---------|----------------|
| Bar history | `BarRenderer.completed_bars` | 1000 per symbol/timeframe | `deque(maxlen)` — auto-trims oldest |
| Order history | `AbstractTradeExecutor._order_history` | 10000 | `deque(maxlen)` — one-time warning when limit reached |
| Trade history | `PortfolioManager._trade_history` | 5000 | `deque(maxlen)` — one-time warning when limit reached |

**Config flow**: `app_config.json` → `AppConfigManager` → `ProcessScenarioConfig` (pickle-safe) → subprocess factories → constructors.

A value of `0` means unlimited (no maxlen). When a limit is reached, a one-time warning is logged: `"⚠️ Order history limit reached (10000). Oldest entries will be discarded. Full history available in scenario log."` The full history remains in the scenario log file for post-analysis.

**Design rationale**: For typical backtesting blocks (6-24h), these limits are never hit. Even aggressive scalping strategies produce ~200 order entries per 24h block. The limits protect against edge cases in very long live sessions or extreme multi-position strategies.

---

## Core Units

### AbstractTradeExecutor
The foundation. Contains all concrete fill processing and shared infrastructure.

**Concrete methods (shared by all modes):**
- `on_tick(tick)` — Unified tick lifecycle: price update + pending order processing
- `_fill_open_order(pending_order, fill_price=None) → None` — Side-effect based: portfolio open, fee calculation, margin check, statistics. Results/rejections stored in `_order_history`. `fill_price` override for live (broker's actual price); `None` for simulation (use current tick bid/ask)
- `_fill_close_order(pending_order, fill_price=None)` — Portfolio close, P&L realization, statistics. Same price override pattern.
- `get_order_history()` — All OrderResults (fills + rejections) for audit trail
- `get_open_positions()` — Returns confirmed portfolio positions only
- `get_account_info()` — Balance, equity, margin, free margin
- All broker queries, symbol specs, statistics collection

**Abstract methods (mode-specific):**
- `_process_pending_orders()` — How pending orders are resolved
- `open_order()` — How orders are submitted
- `close_position()` — How close requests are sent
- `has_pending_orders()` — Whether any orders are in flight
- `is_pending_close(position_id)` — Whether a specific position is being closed
- `close_all_remaining_orders(current_tick)` — End-of-run cleanup: direct-fills open positions via synthetic orders (no pending), then `clear_pending()` for stuck pipeline orders
- `get_pending_stats()` — Aggregated pending order statistics (latency, outcomes)

### AbstractPendingOrderManager
Shared storage and query layer for pending orders. Both execution modes need to track in-flight orders — this base provides the common infrastructure.

**Concrete methods:**
- `store_order(pending_order)` — Add to tracking cache
- `remove_order(order_id)` — Remove from cache (returns the order)
- `get_pending_orders(filter_action=None)` — Query with optional action filter
- `get_pending_count()` — Count pending orders
- `has_pending_orders()` — Any orders in flight?
- `is_pending_close(position_id)` — Specific position being closed?
- `create_synthetic_close_order(position_id)` — Factory for direct-fill close orders that bypass the pipeline (used by `close_all_remaining_orders`)
- `clear_pending(current_tick, reason)` — Cleanup at scenario end, records remaining as FORCE_CLOSED with reason
- `record_outcome(pending_order, outcome, latency_ticks, latency_ms, reason)` — Record resolved pending order for statistics
- `get_pending_stats()` — Return aggregated `PendingOrderStats`

### OrderLatencySimulator (extends AbstractPendingOrderManager)
Simulation-specific pending order manager. Adds tick-based latency modeling with seeded randomness.

**Simulation-specific methods:**
- `submit_open_order()` — Creates PendingOrder with calculated `fill_at_tick`, stores via inherited `store_order()`
- `submit_close_order()` — Same pattern for close orders
- `process_tick(tick_number)` — Returns orders whose `fill_at_tick` has been reached, removes them via inherited `remove_order()`

Uses `SeededDelayGenerator` for deterministic API latency + market execution delays.

### LiveOrderTracker (extends AbstractPendingOrderManager)

> Full documentation: [live_execution_architecture.md](live_execution_architecture.md)

Live-specific pending order manager. Adds broker reference tracking (`_broker_ref_index` for O(1) lookup), timeout detection, and fill/rejection marking from broker responses.

### TradeSimulator (extends AbstractTradeExecutor)
Simulated execution. Delegates pending order management to OrderLatencySimulator.

**Key characteristic:** Orders enter a queue with a seeded delay. After N ticks, they "fill" — and the base class `_fill_open_order()` / `_fill_close_order()` handles the rest. The simulator adds nothing to the fill logic itself.

**Stress testing:** `_stress_test_should_reject()` intercepts orders between latency completion and fill processing. Controlled by module-level constants (`STRESS_TEST_REJECTION_ENABLED`, `STRESS_TEST_REJECT_EVERY_N`). Rejections are stored in `_order_history` with `BROKER_ERROR` reason — same data path as real broker rejections.

`has_pending_orders()` and `is_pending_close()` delegate directly to `self.latency_simulator` (which inherits these from AbstractPendingOrderManager).

### LiveTradeExecutor (extends AbstractTradeExecutor)

> Full documentation: [live_execution_architecture.md](live_execution_architecture.md)

Live execution via broker adapter API. Routes orders through `adapter.execute_order()`, polls broker via `adapter.check_order_status()`, calls the *same* shared fill methods from the base. Delegates pending order management to LiveOrderTracker. MARKET and LIMIT orders supported.

### BaseAdapter (Tiered Interface)
Abstract interface for all broker adapters. Methods are organized in tiers:

**Tier 1 — Required (all brokers):** `create_market_order()`, `create_limit_order()`, `validate_order()`, `get_symbol_specification()`, `get_broker_specification()`

**Tier 2 — Optional (extended orders):** `create_stop_order()`, `create_stop_limit_order()`, `create_iceberg_order()` — default `NotImplementedError`

**Tier 3 — Optional (live execution):** `execute_order()`, `check_order_status()`, `cancel_order()`, `is_live_capable()` — see [live_execution_architecture.md](live_execution_architecture.md)

Adapters that only serve backtesting (KrakenAdapter, MT5Adapter) implement Tier 1+2. Live-capable adapters additionally implement Tier 3.

### MockBrokerAdapter (extends BaseAdapter, for testing)

> Full documentation: [live_execution_architecture.md](live_execution_architecture.md)

Mock adapter in `python/framework/testing/mock_adapter.py`. Implements all three tiers with configurable behavior (INSTANT_FILL, DELAYED_FILL, REJECT_ALL, TIMEOUT). Used by `MockOrderExecution` for testing LiveTradeExecutor without a real broker.

### DecisionTradingAPI
The gatekeeper. DecisionLogic interacts *only* through this API. It provides:

- Order-type validation at startup (fail early, not at tick 50,000)
- Clean method signatures (`send_order`, `get_open_positions`, `close_position`)
- Pending order awareness (`has_pending_orders`, `is_pending_close`)
- Executor-agnostic: works identically with TradeSimulator and LiveTradeExecutor

### PortfolioManager
The single source of truth for position state. Manages:

- Open positions with full fee tracking
- Balance, equity, margin calculations
- Position open/close with P&L realization
- Trade history for post-run analysis

Both simulation and live share the same PortfolioManager. In live mode, it acts as the **local shadow state** — see [live_execution_architecture.md](live_execution_architecture.md) for shadow state and reconciliation details.

### PendingOrder (shared dataclass)
Generic pending order representation used by both modes. Mode-specific fields are Optional:

- **Common fields:** `pending_order_id`, `order_action`, `order_type` (MARKET/LIMIT), `symbol`, `direction`, `lots`, `entry_price` (limit price for LIMIT, 0 for MARKET), `order_kwargs` (built from explicit params: stop_loss, take_profit, comment, magic_number)
- **Simulation fields:** `placed_at_tick`, `fill_at_tick` (tick-based delay tracking)
- **Live fields:** `submitted_at`, `broker_ref`, `timeout_at` — see [live_execution_architecture.md](live_execution_architecture.md)

Each mode sets the fields it needs. The other mode's fields remain None.

---

## Unified Tick Lifecycle

The tick loop (process_tick_loop) calls exactly one method on the executor:

```
trade_executor.on_tick(tick)
```

This single call handles:
1. **Price update** — store current bid/ask, mark portfolio dirty
2. **Pending order processing** — mode-specific (abstract method)

The tick loop does not know (and should not know) whether it's driving a simulator or a live executor. It doesn't call internal methods, doesn't inspect queues, doesn't check pending orders. One call, one responsibility.

### Why This Matters

In the previous design, the tick loop called two separate methods: `update_prices(tick)` and `process_pending_orders()`. This leaked implementation details — the loop "knew" that orders and prices were separate concerns inside the executor. When moving to live trading, this coupling would have required changes in the tick loop itself.

With `on_tick()`, the tick loop is a pure driver. The executor decides how to partition its work internally.

---

## Pending Order Awareness: Two Patterns

When an order is submitted, it doesn't execute instantly. There's a delay (simulated or real). During this delay, the strategy needs to know that something is "in flight" — otherwise it might submit duplicate orders.

The previous approach mixed pending orders into `get_open_positions()` as "pseudo-positions" (Position objects with `pending=True`). This was problematic:

- **Behavior divergence**: Simulation returned pseudo-positions, live trading wouldn't
- **Broken contracts**: `get_open_positions()` returned objects that weren't actually positions
- **Strategy coupling**: Every strategy had to filter `if not position.pending` — mixing execution awareness into decision logic

### The Clean Separation

The new design separates concerns completely:

**`get_open_positions()`** — Returns only confirmed, filled, real portfolio positions. Always. In every mode.

**`has_pending_orders()`** — Global check: "Is anything in flight?" Used by single-position strategies (SimpleConsensus, AggressiveTrend, BacktestingDeterministic) as an early return guard:
```
if self.trading_api.has_pending_orders():
    return None  # Wait for pending orders to resolve
```

**`is_pending_close(position_id)`** — Per-position check: "Is this specific position being closed?" Used by multi-position strategies (BacktestingMultiPosition, BacktestingMarginStress) to avoid duplicate close submissions:
```
if self.trading_api.is_pending_close(pos.position_id):
    continue  # Close already in flight
```

Both methods delegate through DecisionTradingAPI → AbstractTradeExecutor → the respective PendingOrderManager. The logic lives in AbstractPendingOrderManager (shared), queried by the executor, exposed through the API.

---

## Fill Processing: The Shared Core

The fill methods (`_fill_open_order`, `_fill_close_order`) are the heart of the hybrid architecture. They contain the most complex and critical logic:

### Open Fill (`_fill_open_order(pending_order, fill_price=None) → None`)
**Side-effect based** — results are stored in `_order_history`, not returned.

1. Determine entry price: `fill_price` if provided (live: broker's price), else bid/ask from tick (simulation)
2. Look up symbol specification (contract size, digits, tick value)
3. Calculate dynamic tick value (account currency conversion)
4. Create entry fee (spread-based or maker/taker, depending on broker)
5. Check margin availability → **reject if insufficient** (appends rejection to `_order_history`, returns)
6. Open position in PortfolioManager
7. Append `OrderResult` to `_order_history`
8. Update execution statistics (`_orders_executed`, `_total_spread_cost`)

The method is void because both success and failure are side effects: results go into `_order_history`, rejections increment `_orders_rejected`. Callers (like `_process_pending_orders()`) don't need to inspect the result — the portfolio and history are updated internally.

### Close Fill (`_fill_close_order(pending_order, fill_price=None)`)
1. Look up position in portfolio
2. Determine close price: `fill_price` if provided (live), else bid/ask from tick (simulation)
3. Calculate exit tick value
4. Close position in PortfolioManager (realizes P&L)
5. Append `OrderResult` to `_order_history`

The `fill_price` parameter enables the sim→live transition: simulation determines price locally (current tick bid/ask), live receives the actual price from the broker. The rest of the logic is identical.

### Order History (`_order_history`)
All order outcomes — successful fills AND rejections — are recorded in `_order_history` (List[OrderResult]). This is the **single audit trail** for everything that happened to orders after they left the pending queue.

- **Successful open**: OrderResult with status=EXECUTED, position details
- **Margin rejection**: OrderResult with status=REJECTED, reason=INSUFFICIENT_MARGIN
- **Stress test rejection**: OrderResult with status=REJECTED, reason=BROKER_ERROR
- **Successful close**: OrderResult with status=EXECUTED, close details

Exposed via `get_order_history()` and transferred across the subprocess boundary in `ProcessTickLoopResult.order_history`.

**Distinction from `trade_history`**: `trade_history` (from PortfolioManager) contains completed round-trip trades with P&L. `order_history` contains all order attempts. A rejection appears in `order_history` but never in `trade_history` (no position was created). A successful trade appears in both, but only `trade_history` has P&L (calculated at close).

---

## Fill Price: Simulation vs Live

A subtle but critical difference between simulation and live is **who determines the fill price**.

**Simulation:** The system determines the price. When a pending order's delay elapses, `_fill_open_order()` reads the current tick's bid/ask and applies it. The "broker" (simulator) fills at whatever the market shows at fill time.

**Live:** The broker determines the price. The broker returns the actual execution price, which may differ from the last tick we received (slippage, requotes, market gaps). The `fill_price` parameter carries this broker-determined price into the shared fill logic.

```
# Simulation (no fill_price → use current tick):
self._fill_open_order(pending_order)

# Live (broker's actual price):
self._fill_open_order(pending_order, fill_price=broker_response.execution_price)
```

This separation ensures the portfolio always reflects the real execution price, regardless of mode.

---

## Limit Order Lifecycle (Two-Phase)

Limit orders follow a **two-phase lifecycle** in simulation. The order is first accepted by the "broker" (latency simulation), then monitored for price trigger.

### Phase 1: Broker Acceptance (Latency)

```
1. DecisionLogic calls send_order(order_type=LIMIT, price=1.1000)
2. DecisionTradingAPI builds OpenOrderRequest, passes to executor
3. TradeSimulator.open_order(request)
   → Validates price > 0
   → Submits to OrderLatencySimulator with order_type=LIMIT, entry_price=limit_price
   → Returns PENDING
4. OrderLatencySimulator simulates broker acceptance delay (same as market orders)
5. After latency: PendingOrder exits queue with order_type=LIMIT
```

### Phase 2: Price Trigger Monitoring

```
6. _process_pending_orders() checks order_type:
   a) If MARKET → fill immediately at current tick (unchanged behavior)
   b) If LIMIT → check if price already reached:
      - YES → fill immediately as LIMIT_IMMEDIATE (price moved past limit during latency)
      - NO → move to _active_limit_orders list
7. Each tick: iterate _active_limit_orders:
   - LONG limit: ask <= limit_price → fill at limit_price (FillType.LIMIT)
   - SHORT limit: bid >= limit_price → fill at limit_price (FillType.LIMIT)
   - Unfilled orders stay in list
```

### Fill Types

| FillType | Meaning |
|----------|---------|
| `MARKET` | Standard market order fill at current tick price |
| `LIMIT` | Limit order filled when price reached trigger level |
| `LIMIT_IMMEDIATE` | Limit order filled immediately after latency (price already past limit) |

### Entry Types and Fees

Each fill carries an `EntryType` (MARKET or LIMIT) that flows through to `TradeRecord.entry_type` for history/reporting. Limit fills use **maker fees** (lower cost for providing liquidity), market fills use **taker fees**. This distinction only matters for maker/taker fee models (e.g. Kraken). Spread-based brokers (MT5) are unaffected.

### Live Mode

In live mode, the broker handles limit order matching server-side. `LiveTradeExecutor.open_order()` passes the limit price to the broker adapter via `order_kwargs["price"]`. Fill detection happens through the standard broker polling path — no separate `_active_limit_orders` needed.

### Cleanup

At scenario end, `close_all_remaining_orders()` discards unfilled limit orders from `_active_limit_orders` with a warning log. These are orders whose price trigger was never reached.

---

## Open Issues

### Tick-Based → Millisecond-Based Latency
**Problem:** OrderLatencySimulator models delays as tick counts. Real broker latency is time-based. Tick-counting is meaningless in live trading where ticks arrive at irregular intervals.
- Affects: OrderLatencySimulator, SeededDelayGenerator, PendingOrder
- See: `ISSUE_tick_to_ms_latency_migration.md`

### Error Handling in Execution Chain (Partially Resolved)
**Resolved:** `_fill_open_order()` is now void/side-effect based — rejections stored in `_order_history` instead of returned. Margin rejections and stress test rejections follow the same pattern. `order_history` crosses subprocess boundary via `ProcessTickLoopResult`.
**Remaining:** `_fill_close_order()` still returns None silently on position-not-found. `close_position()` result not checked by DecisionLogic. Broader error propagation pattern (timeouts, broker errors in live) still needs design.
- Affects: AbstractTradeExecutor (close path), DecisionTradingAPI, DecisionLogic
- See: `ISSUE_error_handling_execution_chain.md`

### Stress Test Configuration
**Problem:** Stress test rejection in TradeSimulator is controlled by module-level constants (`STRESS_TEST_REJECTION_ENABLED`, `STRESS_TEST_REJECT_EVERY_N`). Needs to be config-file driven for per-scenario control.
- Affects: TradeSimulator, scenario configuration
- See: `ISSUE_stress_test_config.md`

### Baseline Tests: order_history Coverage
**Problem:** Baseline tests validate `execution_stats` counters but don't assert on `order_history` contents. Tests correctly detect stress test rejections (test_no_rejected_orders, test_orders_sent_equals_executed fail when enabled), but no dedicated fixture/assertions for order_history data.
- Affects: Baseline test suite, test fixtures
- See: `ISSUE_baseline_tests_order_history.md`

### Live-Specific Open Issues
See [live_execution_architecture.md](live_execution_architecture.md): Reconciliation Layer, Live Autotrader Pipeline.

---

## Design Decisions Log

### Why Abstract Class, Not Hooks?

We considered a hooks pattern: TradeSimulator stays monolithic, with hook functions (`on_before_fill`, `on_after_submit`) that live mode overrides. This was rejected because:

1. **Too many variation points**: 5+ methods with completely different implementations (submit, close, process_pending, has_pending, is_pending_close). Hooks work for 1-2 customization points, not for swapping half the class.
2. **Unclear ownership**: With hooks, it's ambiguous whether the base or the hook "owns" the fill. With abstract, the inheritance hierarchy is explicit.
3. **Testing**: Abstract classes can be tested via concrete subclasses. Hook-based systems require mocking the hooks, which tests the framework more than the logic.

### Why Pseudo-Positions Were Eliminated

The previous design added pending orders to `get_open_positions()` as Position objects with `pending=True`. This created a **behavior contract that couldn't survive the sim→live transition**:

- In simulation, TradeSimulator could construct pseudo-positions because it controlled the latency queue
- In live trading, there's no local pseudo-position — the broker hasn't confirmed anything yet
- Strategies written against the simulation API would break in live (different position list contents)

The replacement (`has_pending_orders` + `is_pending_close`) provides the same information without contaminating the position list.

### Why Fill Logic Lives in the Base Class

The initial refactoring extracted TradeSimulator into an abstract, but left fill logic in the subclass. This meant LiveTradeExecutor was hollow — `close_position()` raised NotImplementedError, but live trading *needs* the portfolio update logic.

Moving fills to the base was the realization that **fill processing is not simulation-specific**. It's the shared business logic that both modes need. The subclass only decides *when* to call it (after latency delay vs after broker confirmation).

### Why Fill Price Is a Parameter, Not Internal

Originally, `_fill_open_order()` determined the entry price internally from the current tick (ask for LONG, bid for SHORT). This works for simulation but not for live:

- In simulation, the system IS the market — current tick bid/ask is the "broker's" fill price
- In live, the broker returns the actual execution price, which may differ (slippage)

Making `fill_price` an optional parameter keeps backward compatibility (simulation passes nothing, gets tick-based price) while enabling live trading (passes broker's actual price). The portfolio always records the real execution price.

### Why PendingOrderManager Was Extracted

OrderLatencySimulator originally handled both storage/query AND delay simulation. This coupling meant LiveTradeExecutor would need its own separate storage — duplicating the dict, query methods, has_pending logic, etc.

Extracting AbstractPendingOrderManager provides:
- **Shared storage** — both modes use the same dict-based tracking
- **Shared queries** — `has_pending_orders()`, `is_pending_close()` are identical
- **DRY** — no duplicate implementations between simulation and live
- **Testable** — storage/query logic tested once, covers both modes

The split: AbstractPendingOrderManager owns the "what" (storage, query). Subclasses own the "when" (tick-based fill detection vs broker-response detection).

### Why BaseAdapter Was Extended, Not Split

We considered a separate `OrderExecutionAdapter` interface for live execution methods. This was rejected because:

1. **Existing pattern works**: BaseAdapter already has optional methods with `NotImplementedError` defaults (Tier 2: `create_stop_order`, `create_iceberg_order`). Same pattern for Tier 3 execution methods.
2. **No interface pollution**: Backtesting code never calls `execute_order()` — TradeSimulator uses its own latency queue. The methods exist but are never invoked.
3. **One inheritance chain**: `MockBrokerAdapter extends BaseAdapter` — one class provides data, validation, AND execution. No diamond inheritance, no adapter composition.
4. **File organization solves complexity**: As adapters grow, execution logic moves to utility files (`kraken_order_execution.py`) while the adapter class remains the entry point.

---

## Glossary

| Term | Meaning |
|------|---------|
| **Fill** | An order being executed and becoming a position |
| **Fill Price** | The actual execution price — from tick (sim) or broker (live) |
| **Pending Order** | An order submitted but not yet filled (PendingOrder dataclass, shared) |
| **PendingOrderManager** | Abstract storage/query layer for pending orders (AbstractPendingOrderManager) |
| **OrderLatencySimulator** | Simulation-specific pending order manager with seeded tick delays |
| **LiveOrderTracker** | Live-specific pending order manager — see [live_execution_architecture.md](live_execution_architecture.md) |
| **Pseudo-Position** | (Removed) A fake position representing a pending order — now replaced by explicit API |
| **Tick Loop** | The main processing loop that feeds ticks to all components |
| **DecisionLogic** | Trading strategy that produces buy/sell/flat decisions |
| **Worker** | Indicator calculator that feeds data to DecisionLogic |
| **Order History** | Complete audit trail of all order outcomes (fills + rejections) from `_order_history` |
| **BrokerResponse** | Standardized response from broker adapter — see [live_execution_architecture.md](live_execution_architecture.md) |
| **MockBrokerAdapter** | Test adapter with configurable execution modes — see [live_execution_architecture.md](live_execution_architecture.md) |
| **Error Seeds** | Seeded fault injection in simulation for stress testing error-handling paths |
| **PendingOrderOutcome** | Enum: FILLED, REJECTED, TIMED_OUT, FORCE_CLOSED — how a pending order left the queue |
| **PendingOrderStats** | Aggregated latency metrics and outcome counters for all resolved pending orders |
| **PendingOrderRecord** | Individual record for anomalous outcomes (FORCE_CLOSED, TIMED_OUT) only |
| **OpenOrderRequest** | Internal pipeline dataclass bundling all order parameters (symbol, order_type, direction, lots, price, stop_loss, take_profit, comment, magic_number) |
| **EntryType** | How a position was opened: MARKET or LIMIT — stored on TradeRecord for history |
| **FillType** | How an order was filled: MARKET, LIMIT, or LIMIT_IMMEDIATE — stored in OrderResult.metadata |
| **Active Limit Order** | A limit order that passed latency simulation but hasn't triggered yet — sits in `_active_limit_orders` waiting for price |
| **History Limits** | Configurable `deque(maxlen)` caps on order_history, trade_history, bar_history — set via `app_config.json` |
