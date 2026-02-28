# Live Executor Tests Documentation

## Overview

The live executor test suite validates the LiveTradeExecutor, LiveOrderTracker, and MockBrokerAdapter pipeline. All tests run against MockBrokerAdapter — no network, no config files, no tick data files required.

**Test Configuration:** MockBrokerAdapter with real Kraken BTCUSD symbol specification
- Symbol: BTCUSD
- Account Currency: USD
- Initial Balance: 10,000 USD
- Execution Modes: instant_fill, delayed_fill, reject_all, timeout

**Total Tests:** 58

**Location:** `tests/live_executor/`

---

## Test Structure

### Self-Contained Fixture Architecture

Unlike backtesting suites, live executor tests do NOT use shared fixture_helpers or scenario execution. Each test creates its own executor via `MockOrderExecution` — a lightweight test utility that wraps MockBrokerAdapter + LiveTradeExecutor.

```
tests/
├── live_executor/
│   ├── __init__.py
│   ├── conftest.py                        ← Fixtures: mock modes, executor instances, tracker
│   ├── test_live_order_tracker.py         ← Level 1: LiveOrderTracker isolated
│   ├── test_live_executor_mock.py         ← Level 2: LiveTradeExecutor + MockAdapter integration
│   ├── test_live_executor_multi_order.py  ← Level 3: Multi-order scenarios
│   └── test_live_executor_modify.py       ← Level 4: Limit order modification via broker
```

**Why this pattern?**
- No scenario execution overhead — tests run in ~2 seconds
- Each test is fully isolated (function-scoped fixtures)
- MockOrderExecution handles tick feeding and executor creation
- Tests validate the execution pipeline, not backtesting infrastructure

---

## Fixtures (conftest.py)

### Configuration Fixtures

| Fixture | Scope | Description |
|---------|-------|-------------|
| `timeout_config` | function | Standard TimeoutConfig (30s) |
| `logger` | function | GlobalLogger instance for isolated tests |

### LiveOrderTracker Fixtures

| Fixture | Scope | Description |
|---------|-------|-------------|
| `order_tracker` | function | Fresh LiveOrderTracker for unit tests |

### MockOrderExecution Fixtures

| Fixture | Scope | Description |
|---------|-------|-------------|
| `mock_instant` | function | MockOrderExecution in INSTANT_FILL mode |
| `mock_delayed` | function | MockOrderExecution in DELAYED_FILL mode |
| `mock_reject` | function | MockOrderExecution in REJECT_ALL mode |
| `mock_timeout` | function | MockOrderExecution in TIMEOUT mode |

### LiveTradeExecutor Fixtures

| Fixture | Scope | Description |
|---------|-------|-------------|
| `executor_instant` | function | LiveTradeExecutor with instant fill adapter |
| `executor_delayed` | function | LiveTradeExecutor with delayed fill adapter |
| `executor_reject` | function | LiveTradeExecutor with reject-all adapter |

---

## Test Files

### test_live_order_tracker.py (21 Tests)

Tests the LiveOrderTracker independently from LiveTradeExecutor. Validates pending order storage, broker reference index, fill/rejection marking, timeout detection, and cleanup.

#### TestSubmitAndQuery

| Test | Description |
|------|-------------|
| `test_submit_order_tracked` | Submitted order appears in pending orders |
| `test_submit_order_returns_order_id` | submit_order() returns order_id for chaining |
| `test_broker_ref_lookup` | Broker ref index provides O(1) lookup with correct fields |
| `test_unknown_broker_ref_returns_none` | Unknown broker_ref returns None |
| `test_pending_order_has_live_fields` | Submitted order has submitted_at, broker_ref, timeout_at |
| `test_pending_order_action_is_open` | Open order has action=OPEN |

#### TestMarkFilled

| Test | Description |
|------|-------------|
| `test_mark_filled_returns_pending_order` | mark_filled() returns PendingOrder for fill processing |
| `test_mark_filled_removes_from_pending` | Filled order removed from pending storage and index |
| `test_mark_filled_unknown_ref_returns_none` | Unknown broker_ref returns None |

#### TestMarkRejected

| Test | Description |
|------|-------------|
| `test_mark_rejected_returns_pending_order` | mark_rejected() returns PendingOrder for rejection recording |
| `test_mark_rejected_removes_from_pending` | Rejected order removed from pending |
| `test_mark_rejected_unknown_ref_returns_none` | Unknown broker_ref returns None |

#### TestTimeoutDetection

| Test | Description |
|------|-------------|
| `test_no_timeouts_within_window` | Orders within timeout window are not flagged |
| `test_timeout_detected_after_expiry` | Orders past timeout_at are detected (0s timeout) |
| `test_timeout_does_not_remove_order` | check_timeouts() returns but does NOT remove orders |

#### TestCloseOrderTracking

| Test | Description |
|------|-------------|
| `test_submit_close_order_tracked` | Close order appears in pending, is_pending_close() returns True |
| `test_close_order_action_is_close` | Close order has action=CLOSE |
| `test_is_pending_close_false_for_open` | is_pending_close() returns False for open orders |

#### TestClearPending

| Test | Description |
|------|-------------|
| `test_clear_removes_all_orders` | clear_pending() removes all pending orders |
| `test_clear_also_clears_broker_ref_index` | clear_pending() also clears broker_ref index |

---

### test_live_executor_mock.py (19 Tests)

Integration tests for the full execution pipeline: open_order() -> broker response -> fill processing -> order_history / portfolio update.

#### TestInstantFill

| Test | Description |
|------|-------------|
| `test_open_order_returns_executed` | open_order() returns EXECUTED with fill price and broker_order_id |
| `test_instant_fill_creates_position` | Instant fill creates position in portfolio (BTCUSD LONG) |
| `test_order_history_recorded` | Executed order appears in order history with EXECUTED status |
| `test_no_pending_after_instant_fill` | No pending orders remain after instant fill |

#### TestInstantFillClose

| Test | Description |
|------|-------------|
| `test_close_position_returns_executed` | close_position() returns EXECUTED for instant fill |
| `test_close_removes_from_open_positions` | Closed position no longer in open positions |

#### TestDelayedFill

| Test | Description |
|------|-------------|
| `test_open_order_returns_pending` | open_order() returns PENDING in delayed mode |
| `test_pending_order_tracked` | Delayed order tracked as pending |
| `test_delayed_fill_on_next_tick` | Pending order fills when next tick triggers _process_pending_orders() |

#### TestRejection

| Test | Description |
|------|-------------|
| `test_rejected_order_status` | open_order() returns REJECTED in reject_all mode |
| `test_rejected_order_in_history` | Rejected order recorded in order history |
| `test_no_position_created_on_rejection` | No position created for rejected order |

#### TestFeatureGating

| Test | Description |
|------|-------------|
| `test_limit_order_rejected` | LIMIT order rejected with ORDER_TYPE_NOT_SUPPORTED |
| `test_stop_order_rejected` | STOP order rejected |

#### TestValidation

| Test | Description |
|------|-------------|
| `test_invalid_symbol_rejected` | Unknown symbol rejected |
| `test_close_nonexistent_position_rejected` | Closing non-existent position rejected |

#### TestExecutionStats

| Test | Description |
|------|-------------|
| `test_stats_after_instant_fill` | Stats reflect completed order (orders_sent, orders_executed) |
| `test_stats_after_rejection` | Stats count rejections |

#### TestNotLiveCapable

| Test | Description |
|------|-------------|
| `test_non_live_adapter_raises` | LiveTradeExecutor raises ValueError for non-live adapter |

---

### test_live_executor_multi_order.py (7 Tests)

Multi-order scenarios: multiple orders tracked, open+close cycles, close_all_remaining, stats consistency.

#### TestMultipleOrdersTracked

| Test | Description |
|------|-------------|
| `test_two_orders_both_fill` | Two instant-fill orders both create positions |
| `test_multiple_delayed_fills` | Multiple delayed orders all fill after tick |
| `test_order_history_tracks_all` | Order history contains entries for all submitted orders |

#### TestOpenCloseCycle

| Test | Description |
|------|-------------|
| `test_open_close_cycle_completes` | Full open -> close cycle with portfolio verification |
| `test_trade_history_after_close` | Closed trade appears in trade history |

#### TestCloseAllRemaining

| Test | Description |
|------|-------------|
| `test_close_all_closes_open_positions` | close_all_remaining_orders() closes all positions |
| `test_close_all_on_empty_portfolio` | close_all_remaining_orders() handles empty portfolio |

#### TestStatsConsistency

| Test | Description |
|------|-------------|
| `test_sent_equals_executed_plus_rejected` | orders_sent == orders_executed + orders_rejected |

---

### test_live_executor_modify.py (11 Tests)

Limit order modification via broker adapter: successful modify, non-existent order, broker rejection, adapter exceptions, and `get_broker_ref()` reverse lookup.

#### TestModifyLimitOrderSuccess

| Test | Description |
|------|-------------|
| `test_modify_pending_order_price` | modify_limit_order() succeeds for tracked pending order |
| `test_modify_pending_order_sl_tp` | Modify SL and TP on pending order |
| `test_modify_with_unset_keeps_current` | UNSET parameters translated to None (no change) |

#### TestModifyLimitOrderNotFound

| Test | Description |
|------|-------------|
| `test_modify_nonexistent_order` | Returns LIMIT_ORDER_NOT_FOUND for unknown order_id |
| `test_modify_after_fill_returns_not_found` | Returns NOT_FOUND after order has been filled |

#### TestModifyLimitOrderBrokerRejection

| Test | Description |
|------|-------------|
| `test_broker_rejects_modify` | Returns failure when broker rejects modification |

#### TestModifyLimitOrderAdapterException

| Test | Description |
|------|-------------|
| `test_adapter_exception_handled` | Adapter exceptions handled gracefully |

#### TestGetBrokerRefReverseLookup

| Test | Description |
|------|-------------|
| `test_get_broker_ref_returns_ref` | get_broker_ref() returns broker_ref for known order_id |
| `test_get_broker_ref_unknown_returns_none` | Returns None for unknown order_id |
| `test_get_broker_ref_after_fill_returns_none` | Returns None after order filled (removed from index) |
| `test_get_broker_ref_multiple_orders` | Correct ref returned with multiple tracked orders |

---

## Running the Tests

```bash
# Live executor suite only
pytest tests/live_executor/ -v

# Specific test file
pytest tests/live_executor/test_live_order_tracker.py -v

# Specific test class
pytest tests/live_executor/test_live_executor_mock.py::TestInstantFill -v
```

---

## Architecture Notes

### Test Design Philosophy

The live executor tests use **direct pipeline validation** rather than scenario execution:

1. MockBrokerAdapter simulates broker behavior (4 configurable modes)
2. MockOrderExecution provides pre-configured executors and tick feeding
3. Each test validates a specific execution path through the pipeline
4. No external dependencies — tests run in under 2 seconds

### Key Data Flow

```
MockOrderExecution
  +-- MockBrokerAdapter (extends AbstractAdapter)
  |     +-- execute_order() -> BrokerResponse
  |     +-- check_order_status() -> BrokerResponse
  |     +-- cancel_order() -> BrokerResponse
  |     +-- modify_order() -> BrokerResponse
  |
  +-- LiveTradeExecutor (extends AbstractTradeExecutor)
        +-- open_order() -> OrderResult
        +-- close_position() -> OrderResult
        +-- modify_limit_order() -> ModificationResult
        +-- on_tick() -> _process_pending_orders()
        |     +-- LiveOrderTracker.get_pending_orders()
        |     +-- adapter.check_order_status()
        |     +-- _fill_open_order() / _fill_close_order() (inherited)
        +-- get_order_history() -> List[OrderResult]
        +-- get_execution_stats() -> ExecutionStats
```

### MockBrokerAdapter Modes

| Mode | execute_order() | check_order_status() | modify_order() | Use Case |
|------|----------------|---------------------|----------------|----------|
| `INSTANT_FILL` | Returns FILLED | N/A (never pending) | FILLED if pending | Synchronous broker APIs |
| `DELAYED_FILL` | Returns PENDING | Returns FILLED on first check | FILLED if pending | Asynchronous broker APIs |
| `REJECT_ALL` | Returns REJECTED | N/A (never pending) | Returns REJECTED | Error handling paths |
| `TIMEOUT` | Returns PENDING | Always PENDING | FILLED if pending | Timeout detection |

### Bug Found During Test Development

`LiveOrderTracker.submit_order()` set `order_kwargs=None` when no kwargs were passed.
The inherited `_fill_open_order()` in `AbstractTradeExecutor` called `.get()` on this None value.
Fix: Changed to `order_kwargs=kwargs if kwargs else {}` (empty dict instead of None).
