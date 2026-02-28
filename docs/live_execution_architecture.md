# Live Execution Architecture

Live trading execution via broker adapter API. This document covers all live-specific components, the broker polling flow, and the live-specific open issues.

For shared architecture (AbstractTradeExecutor, fill processing, portfolio, design decisions): see [architecture_execution_layer.md](architecture_execution_layer.md)
For tick flow comparison (Backtesting vs Live): see [simulation_vs_live_flow.md](simulation_vs_live_flow.md)

---

## LiveTradeExecutor (extends AbstractTradeExecutor)

Live execution via broker adapter API. Delegates pending order management to LiveOrderTracker.

**Key characteristic:** Routes orders through `adapter.execute_order()`, polls broker via `adapter.check_order_status()`, and calls the *same* `_fill_open_order(pending_order, fill_price=broker_price)` / `_fill_close_order(pending_order, fill_price=broker_price)` from the base — identical portfolio logic, zero duplication.

**Constructor validation:** Requires `adapter.is_live_capable() == True`. Takes optional `TimeoutConfig` (default: 30s timeout).

**Order flow:**
1. `open_order()` — Validates, calls `adapter.execute_order()`, handles immediate fill/rejection/pending
2. `_process_pending_orders()` — Polls broker for each pending order, handles fills/rejections/timeouts
3. `_handle_broker_response()` — Dispatches FILLED → `_fill_open_order()`, REJECTED → `_order_history`
4. `_handle_timeout()` — Cancels at broker, records BROKER_ERROR rejection

**Feature gating:** MARKET and LIMIT orders supported. Extended order types (STOP, STOP_LIMIT) are rejected.

**Testable via MockBrokerAdapter** — no real broker needed for pipeline verification.

---

## LiveOrderTracker (extends AbstractPendingOrderManager)

Live-specific pending order manager. Adds broker reference tracking, timeout detection, and fill/rejection marking from broker responses.

**Internal state:** `_broker_ref_index: Dict[str, str]` maps broker_ref → order_id for O(1) lookup when broker responds.

**Live-specific methods:**
- `submit_order(order_id, symbol, direction, lots, broker_ref, order_kwargs=None)` — Creates PendingOrder with `submitted_at`, `broker_ref`, `timeout_at`. Indexes by broker_ref.
- `submit_close_order(position_id, broker_ref, close_lots)` — Same pattern for close orders
- `mark_filled(broker_ref, fill_price, filled_lots)` — Removes from pending, returns PendingOrder for fill processing
- `mark_rejected(broker_ref, reason)` — Removes from pending, returns PendingOrder for rejection recording
- `check_timeouts()` — Returns orders past `timeout_at` (does not remove — caller decides)
- `get_by_broker_ref(broker_ref)` — O(1) lookup via broker reference index
- `clear_pending()` — Override: clears both pending orders and broker_ref index

---

## Broker Reference Lifecycle (Poll-Flow)

The `broker_ref` is the bridge between our internal order tracking and the broker's system. This is the concrete flow for a live order from submission to fill:

### 1. Order Submission → broker_ref Received

```
LiveTradeExecutor.open_order(request: OpenOrderRequest)
    │
    ├── adapter.execute_order(symbol, direction, lots, order_type, **order_kwargs)
    │   └── BrokerResponse(broker_ref="MT5-12345", status=PENDING)
    │
    └── order_tracker.submit_order(
            order_id="USDJPY_1",          # internal ID
            broker_ref="MT5-12345",        # broker's reference
            symbol="USDJPY", direction=LONG, lots=0.1
        )
        └── _broker_ref_index["MT5-12345"] = "USDJPY_1"   # O(1) index
```

### 2. Next Tick → Broker Polling

```
live_executor.on_tick(tick)
    └── _process_pending_orders()
        │
        ├── for pending in order_tracker.get_pending_orders():
        │       │
        │       └── adapter.check_order_status(pending.broker_ref)
        │           └── BrokerResponse(status=FILLED, fill_price=156.500)
        │
        └── _handle_broker_response(pending, response)
                │
                ├── FILLED:
                │   ├── order_tracker.mark_filled(broker_ref="MT5-12345", fill_price=156.500)
                │   │   └── removes from _pending_orders + _broker_ref_index
                │   │   └── returns PendingOrder for fill processing
                │   └── _fill_open_order(pending, fill_price=156.500)   # shared base method
                │
                ├── REJECTED:
                │   ├── order_tracker.mark_rejected(broker_ref, reason)
                │   └── rejection → _order_history
                │
                └── PENDING: no action, keep polling next tick
```

### 3. Timeout Detection

```
_process_pending_orders()
    └── timed_out = order_tracker.check_timeouts()
        └── for pending in timed_out:
                ├── adapter.cancel_order(pending.broker_ref)   # try to cancel at broker
                ├── order_tracker.mark_rejected(broker_ref, reason="order_timeout")
                └── rejection → _order_history (BROKER_ERROR)
```

### 4. Immediate Fill (Synchronous Broker)

Some brokers fill market orders synchronously. In that case:

```
adapter.execute_order(...)
    └── BrokerResponse(status=FILLED, fill_price=156.500, broker_ref="MT5-12345")

LiveTradeExecutor:
    ├── order_tracker.submit_order(...)        # register briefly
    ├── order_tracker.mark_filled(...)         # immediately mark filled
    └── _fill_open_order(pending, fill_price)  # process fill
    └── return OrderResult(status=EXECUTED)     # caller sees instant fill
```

---

## Live Error Handling

Error handling follows the same patterns as simulation, using the shared infrastructure from AbstractTradeExecutor:

```
1. open_order() → adapter.execute_order() → PendingOrder in LiveOrderTracker
2. Broker doesn't respond / rejects
3. _process_pending_orders() polls adapter → detects timeout / rejection
4. Same handling logic as simulation stress test
```

The advantage: Error-handling logic is tested in the simulator first (stress test: "reject every 3rd trade"), then runs identically in live with real broker errors.

---

## PendingOrder: Live-Specific Fields

The shared `PendingOrder` dataclass has optional fields for each mode. Live sets:

- `submitted_at: datetime` — UTC timestamp when order was sent to broker
- `broker_ref: str` — Broker's order reference (MT5 ticket, Kraken order ID)
- `timeout_at: datetime` — When to consider the order timed out

Simulation fields (`placed_at_tick`, `fill_at_tick`) remain None in live mode.

---

## BaseAdapter: Live Execution Interface (Tier 3)

The `BaseAdapter` abstract class organizes methods in tiers. Live execution is Tier 3:

**Tier 3 — Optional (live execution):** `execute_order()`, `check_order_status()`, `cancel_order()`, `modify_order()`, `is_live_capable()` — default `NotImplementedError` / `False`

Adapters that only serve backtesting (KrakenAdapter, MT5Adapter) implement Tier 1+2. Live-capable adapters additionally implement Tier 3.

### MockBrokerAdapter (extends BaseAdapter, for testing)

Mock adapter in `python/framework/testing/mock_adapter.py`. Implements all three tiers with configurable behavior. Uses real Kraken BTCUSD symbol specification.

**Execution modes (MockExecutionMode):**
- `INSTANT_FILL` — `execute_order()` returns FILLED immediately
- `DELAYED_FILL` — Returns PENDING, `check_order_status()` returns FILLED on next call
- `REJECT_ALL` — Returns REJECTED
- `TIMEOUT` — Returns PENDING, `check_order_status()` stays PENDING forever

Used by `MockOrderExecution` utility (`python/framework/testing/mock_order_execution.py`) to create pre-configured LiveTradeExecutor instances for testing.

---

## Live Limit Order Modification

`LiveTradeExecutor.modify_limit_order()` modifies pending limit orders at the broker via `adapter.modify_order()`.

### Flow

```
modify_limit_order(order_id, new_price, new_sl, new_tp)
    │
    ├── 1. LiveOrderTracker.get_broker_ref(order_id)
    │      → None? → LIMIT_ORDER_NOT_FOUND
    │
    ├── 2. UNSET → None translation (adapter uses None=no change)
    │
    ├── 3. adapter.modify_order(broker_ref, new_price, new_sl, new_tp)
    │      → Exception? → INVALID_PRICE
    │
    ├── 4. BrokerResponse.is_rejected?
    │      → Yes: INVALID_PRICE
    │
    └── 5. Success → ModificationResult(success=True)
```

### Design Notes

- **No local SL/TP validation** — broker handles validation server-side. Simulation validates locally against limit price; live delegates to broker.
- **No local shadow state update** — LiveTradeExecutor has no `_active_limit_orders` queue (that lives in TradeSimulator only). When the order lifecycle is lifted into AbstractTradeExecutor (#133 Step 4), local shadow state updates will be added after successful broker modify.
- **UNSET sentinel** — The `_UnsetType`/`UNSET` pattern from `PortfolioManager` is translated to `None` at the adapter boundary. Adapters don't know about UNSET.
- **`get_broker_ref(order_id)`** — Reverse lookup on `LiveOrderTracker._broker_ref_index` (O(n) scan). The forward lookup `get_by_broker_ref(broker_ref)` is O(1) and used in the polling flow.

### MockBrokerAdapter.modify_order()

Mock behavior:
- `REJECT_ALL` mode: Returns REJECTED
- Other modes: Returns FILLED (modification accepted) if broker_ref exists in `_mock_pending`
- Unknown broker_ref: Returns REJECTED

---

## PortfolioManager in Live Mode

Both simulation and live share the same PortfolioManager. In live mode, it acts as the **local shadow state** — the system's internal view of what the broker should have. Reconciliation between shadow state and actual broker state is an open issue (see below).

---

## Open Issues (Live-Specific)

### Reconciliation Layer for Live Trading
**Problem:** Local portfolio (shadow state) can diverge from broker's actual state. No mechanism to detect or correct divergence. Required before live trading goes operational.
- Affects: LiveTradeExecutor, PortfolioManager
- See: `ISSUE_reconciliation_layer_live_trading.md`

### Live Autotrader Pipeline
**Next step:** Build FiniexAutoTrader (live runner) that connects tick source → workers → decision logic → LiveTradeExecutor. The execution layer (LiveTradeExecutor, LiveOrderTracker, MockBrokerAdapter) is complete and tested (47 tests). Missing: live runner, Kraken tick source, KrakenAdapter Tier 3.
- Replaces: GitHub Issue #133
- See: `ISSUE_live_autotrader_pipeline.md`

---

## Glossary (Live-Specific Terms)

| Term | Meaning |
|------|---------|
| **LiveOrderTracker** | Live-specific pending order manager with broker tracking |
| **Shadow State** | Local portfolio tracking what we believe the broker state to be |
| **Reconciliation** | Comparing shadow state with actual broker state and resolving differences |
| **BrokerResponse** | Standardized response from broker adapter (fill, rejection, status) |
| **TimeoutConfig** | Configurable thresholds for order timeout detection |
| **MockBrokerAdapter** | Test adapter with configurable execution modes (instant_fill, reject_all, etc.) |
| **broker_ref** | Broker's order reference string (MT5 ticket number, Kraken order ID) — bridges internal tracking to broker system |
