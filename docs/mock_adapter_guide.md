# Mock Adapter Guide: Testing LiveTradeExecutor

## Overview

The MockBrokerAdapter simulates broker responses for testing the LiveTradeExecutor pipeline **without** a real broker connection. It uses real Kraken symbol specifications (BTCUSD) and supports configurable execution behavior.

**Key principle:** Same code pipeline as live trading, deterministic and local.

```
open_order() → adapter.execute_order() → BrokerResponse → LiveOrderTracker → _fill_open_order() → Portfolio
     │                  │                       │                  │                    │
     │              MockBroker              configurable        time-based          INHERITED
     │              (no network)            (4 modes)           tracking            (shared core)
```

---

## Components

| Component | Location | Purpose |
|-----------|----------|---------|
| `MockBrokerAdapter` | `python/framework/testing/mock_adapter.py` | Simulates broker API responses |
| `MockOrderExecution` | `python/framework/testing/mock_order_execution.py` | Test utility: creates executor + feeds ticks |
| `MockExecutionMode` | `python/framework/testing/mock_adapter.py` | Enum: execution behavior modes |
| `BrokerResponse` | `python/framework/types/live_execution_types.py` | Standardized broker reply |
| `TimeoutConfig` | `python/framework/types/live_execution_types.py` | Timeout thresholds |

---

## Execution Modes

### INSTANT_FILL
`execute_order()` returns `BrokerResponse(status=FILLED)` immediately.
- Simulates: Kraken market orders (typically fill instantly)
- Use for: Basic pipeline verification, open+close cycles

### DELAYED_FILL
`execute_order()` returns `BrokerResponse(status=PENDING)`.
`check_order_status()` returns `FILLED` on next call.
- Simulates: Orders that need one polling cycle to confirm
- Use for: Testing `_process_pending_orders()` polling logic

### REJECT_ALL
`execute_order()` returns `BrokerResponse(status=REJECTED)`.
- Simulates: Broker-side rejections (insufficient funds, market closed, etc.)
- Use for: Rejection handling, `_order_history` recording

### TIMEOUT
`execute_order()` returns `BrokerResponse(status=PENDING)`.
`check_order_status()` always returns `PENDING` (never fills).
- Simulates: Unresponsive broker, network issues
- Use for: Timeout detection via `LiveOrderTracker.check_timeouts()`

---

## Usage

### Quick Start — Instant Fill Cycle

```python
from python.framework.testing.mock_order_execution import MockOrderExecution
from python.framework.testing.mock_adapter import MockExecutionMode
from python.framework.types.order_types import OpenOrderRequest, OrderType, OrderDirection

mock = MockOrderExecution(mode=MockExecutionMode.INSTANT_FILL, initial_balance=10000.0)
executor = mock.create_executor()

# Feed a tick (required: sets price state)
mock.feed_tick(executor, symbol="BTCUSD", bid=49999.0, ask=50001.0)

# Place an order
request = OpenOrderRequest(
    symbol="BTCUSD", order_type=OrderType.MARKET,
    direction=OrderDirection.LONG, lots=0.001
)
result = executor.open_order(request)
# result.status == OrderStatus.EXECUTED
# result.executed_price == 50001.0

# Check portfolio
positions = executor.get_open_positions()
# len(positions) == 1

# Close position
mock.feed_tick(executor, symbol="BTCUSD", bid=51000.0, ask=51002.0)
close_result = executor.close_position(result.order_id)

# Check trade history (completed round-trip with P&L)
trade_history = executor.get_trade_history()
```

### Delayed Fill — Testing Polling

```python
mock = MockOrderExecution(mode=MockExecutionMode.DELAYED_FILL)
executor = mock.create_executor()

mock.feed_tick(executor, symbol="BTCUSD", bid=49999.0, ask=50001.0)
request = OpenOrderRequest(
    symbol="BTCUSD", order_type=OrderType.MARKET,
    direction=OrderDirection.LONG, lots=0.001
)
result = executor.open_order(request)
# result.status == OrderStatus.PENDING
# executor.has_pending_orders() == True

# Next tick triggers _process_pending_orders() → polls adapter → fills
mock.feed_tick(executor, symbol="BTCUSD", bid=50100.0, ask=50102.0)
# executor.has_pending_orders() == False
# len(executor.get_open_positions()) == 1
```

### Rejection — Testing Error Path

```python
mock = MockOrderExecution(mode=MockExecutionMode.REJECT_ALL)
executor = mock.create_executor()

mock.feed_tick(executor, symbol="BTCUSD", bid=49999.0, ask=50001.0)
request = OpenOrderRequest(
    symbol="BTCUSD", order_type=OrderType.MARKET,
    direction=OrderDirection.LONG, lots=0.001
)
result = executor.open_order(request)
# result.status == OrderStatus.REJECTED
# result.rejection_reason == RejectionReason.BROKER_ERROR

# Check stats
stats = executor.get_execution_stats()
# stats.orders_sent == 1
# stats.orders_rejected == 1
# stats.orders_executed == 0
```

### Direct MockBrokerAdapter Access

For lower-level control (changing mode mid-test, slippage simulation):

```python
from python.framework.testing.mock_adapter import MockBrokerAdapter, MockExecutionMode
from python.framework.trading_env.broker_config import BrokerConfig
from python.framework.trading_env.live.live_trade_executor import LiveTradeExecutor
from python.framework.types.broker_types import BrokerType

adapter = MockBrokerAdapter(mode=MockExecutionMode.INSTANT_FILL)
adapter.set_slippage(5.0)  # +5 points on every fill

broker_config = BrokerConfig(BrokerType.KRAKEN_SPOT, adapter)
executor = LiveTradeExecutor(broker_config, initial_balance=10000.0,
                              account_currency="USD", logger=logger)

# ... execute orders ...

# Change mode mid-test
adapter.set_mode(MockExecutionMode.REJECT_ALL)
# Next order will be rejected
```

---

## Verification Strategies

### 1. Pipeline Verification (does data flow correctly?)
- Order → Adapter → LiveOrderTracker → Fill Processing → Portfolio → Order History
- Check: `get_open_positions()`, `get_order_history()`, `get_execution_stats()`

### 2. Error Path Verification (do rejections propagate?)
- REJECT_ALL mode: rejection in `_order_history`, no position in portfolio
- TIMEOUT mode: timeout detected, cancellation attempted, BROKER_ERROR recorded

### 3. Stats Consistency Verification
- `orders_sent == orders_executed + orders_rejected` (always)
- `len(order_history) >= orders_sent` (may include internal rejections)
- `len(trade_history) <= orders_executed` (only completed round-trips)

### 4. Sim vs Live Comparison (same shared core?)
- Same order sequence through TradeSimulator and LiveTradeExecutor(Mock)
- Compare: position count, execution stats, fee calculations
- Proves the shared-core architecture works identically across modes

---

## Mock Symbol Configuration

Default mock config uses real Kraken BTCUSD specification:

| Property | Value |
|----------|-------|
| Symbol | BTCUSD |
| Base Currency | BTC |
| Quote Currency | USD |
| Contract Size | 1 |
| Volume Min | 0.00005 |
| Volume Max | 10000 |
| Volume Step | 1e-8 |
| Tick Size | 0.1 |
| Digits | 1 |
| Fee Model | Maker/Taker (0.16% / 0.26%) |
| Leverage | 1 (spot) |

---

## Test Suite

Full pytest test suite available at `tests/live_executor/` (47 tests):

| File | Tests | Scope |
|------|-------|-------|
| `test_live_order_tracker.py` | 21 | LiveOrderTracker isolated (submit, fill, reject, timeout, cleanup) |
| `test_live_executor_mock.py` | 19 | LiveTradeExecutor + MockAdapter integration (all 4 modes) |
| `test_live_executor_multi_order.py` | 7 | Multi-order scenarios (open+close, close_all, stats consistency) |

```bash
pytest tests/live_executor/ -v
```

Full test documentation: `docs/tests/tests_live_executor_docs.md`

---

## File Map

```
python/framework/
  testing/
    __init__.py
    mock_adapter.py              ← MockBrokerAdapter, MockExecutionMode
    mock_order_execution.py      ← MockOrderExecution utility
  trading_env/
    live/
      live_trade_executor.py     ← LiveTradeExecutor (uses adapter)
      live_order_tracker.py      ← LiveOrderTracker (time-based pending)
    adapters/
      abstract_adapter.py        ← Tier 3: execute_order, check_order_status, cancel_order
  types/
    live_execution_types.py      ← BrokerResponse, BrokerOrderStatus, TimeoutConfig
  exceptions/
    live_execution_errors.py     ← BrokerConnectionError, OrderTimeoutError
  factory/
    live_trade_executor_factory.py ← build_live_executor()
```
