# Live Executor Tests Documentation

## Overview

The live executor test suite validates the LiveTradeExecutor, LiveRequestProcessor, and MockBrokerAdapter pipeline. All tests run against MockBrokerAdapter — no network, no config files, no tick data files required.

**Test Configuration:** MockBrokerAdapter with real Kraken BTCUSD symbol specification
- Symbol: BTCUSD
- Account Currency: USD
- Initial Balance: 10,000 USD
- Execution Modes: instant_fill, delayed_fill, reject_all, timeout

**Total Tests:** 67

**Location:** `tests/autotrader/live_executor/`

---

## Test Structure

### Self-Contained Fixture Architecture

Unlike backtesting suites, live executor tests do NOT use shared fixture_helpers or scenario execution. Each test creates its own executor via `MockOrderExecution` — a lightweight test utility that wraps MockBrokerAdapter + LiveTradeExecutor.

```
tests/autotrader/live_executor/
├── __init__.py
├── conftest.py                         ← Fixtures: mock modes, executor instances, processor
├── test_live_request_processor.py      ← Level 1: LiveRequestProcessor storage layer isolated
├── test_live_executor_mock.py          ← Level 2: LiveTradeExecutor + MockAdapter integration
├── test_live_executor_multi_order.py   ← Level 3: Multi-order scenarios
├── test_live_executor_modify.py        ← Level 4: Limit order modification via broker
└── test_async_submit.py                ← Level 5: Async submit lifecycle regressions (#321)
```

**Why this pattern?**
- No scenario execution overhead — tests run in ~8 seconds
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

### LiveRequestProcessor Fixtures

| Fixture | Scope | Description |
|---------|-------|-------------|
| `request_processor` | function | Fresh LiveRequestProcessor for storage-layer unit tests |

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
| `executor_timeout` | function | LiveTradeExecutor with TIMEOUT adapter (orders never fill) |

---

## Test Files

### test_live_request_processor.py (20 Tests)

Tests the `LiveRequestProcessor` storage layer independently from `LiveTradeExecutor`. Validates pending order storage, broker reference index, fill/rejection marking, timeout detection, and cleanup. The orchestration surface (`submit_open_order`, `submit_open_order_async`, `modify_order_sync`, etc.) is covered by the executor-level tests; this suite focuses on the inherited `AbstractPendingOrderManager` storage layer extended with broker-ref tracking.

#### TestSubmitAndQuery

| Test | Description |
|------|-------------|
| `test_register_pending_tracked` | Registered order appears in pending orders |
| `test_register_pending_returns_order_id` | `register_pending_open()` returns order_id for chaining |
| `test_broker_ref_lookup` | Broker ref index provides O(1) lookup with correct fields |
| `test_unknown_broker_ref_returns_none` | Unknown broker_ref returns None |
| `test_pending_order_has_live_fields` | Registered order has submitted_at, broker_ref, timeout_at |
| `test_pending_order_action_is_open` | Open order has action=OPEN |

#### TestMarkFilled

| Test | Description |
|------|-------------|
| `test_mark_filled_returns_pending_order` | `mark_filled()` returns PendingOrder for fill processing |
| `test_mark_filled_removes_from_pending` | Filled order removed from pending storage and index |
| `test_mark_filled_unknown_ref_returns_none` | Unknown broker_ref returns None |

#### TestMarkRejected

| Test | Description |
|------|-------------|
| `test_mark_rejected_returns_pending_order` | `mark_rejected()` returns PendingOrder for rejection recording |
| `test_mark_rejected_removes_from_pending` | Rejected order removed from pending |
| `test_mark_rejected_unknown_ref_returns_none` | Unknown broker_ref returns None |

#### TestTimeoutDetection

| Test | Description |
|------|-------------|
| `test_no_timeouts_within_window` | Orders within timeout window are not flagged |
| `test_timeout_detected_after_expiry` | Orders past timeout_at are detected (0s timeout) |
| `test_timeout_does_not_remove_order` | `check_timeouts()` returns but does NOT remove orders |

#### TestCloseOrderTracking

| Test | Description |
|------|-------------|
| `test_register_pending_close_tracked` | Close order appears in pending, `is_pending_close()` returns True |
| `test_close_order_action_is_close` | Close order has action=CLOSE |
| `test_is_pending_close_false_for_open` | `is_pending_close()` returns False for open orders |

#### TestClearPending

| Test | Description |
|------|-------------|
| `test_clear_removes_all_orders` | `clear_pending()` removes all pending orders |
| `test_clear_also_clears_broker_ref_index` | `clear_pending()` also clears broker_ref index |

---

### test_live_executor_mock.py (18 Tests)

Integration tests for the full execution pipeline: `open_order()` → broker response → fill processing → order_history / portfolio update. All MARKET submits are async post-#319 step 6 — `open_order()` returns PENDING immediately and the fill arrives via `drain_inbox()` on the next tick.

#### TestInstantFill

| Test | Description |
|------|-------------|
| `test_open_order_returns_pending_initially` | `open_order()` returns PENDING immediately under async submit |
| `test_instant_fill_creates_position` | Instant fill creates position after next tick drain |
| `test_order_history_recorded` | Executed order appears in order history (post-drain) |
| `test_no_pending_after_instant_fill` | Pending clears once the worker fill is drained |

#### TestInstantFillClose

| Test | Description |
|------|-------------|
| `test_close_position_returns_executed` | `close_position()` returns PENDING; FILLED after drain |
| `test_close_removes_from_open_positions` | Closed position no longer in open positions |

#### TestDelayedFill

| Test | Description |
|------|-------------|
| `test_open_order_returns_pending` | `open_order()` returns PENDING in delayed mode |
| `test_pending_order_tracked` | Delayed order tracked as pending |
| `test_delayed_fill_on_next_tick` | Pending order fills via Phase-1 polling on next tick |

#### TestRejection

| Test | Description |
|------|-------------|
| `test_rejected_order_status` | Async REJECTED routed via drain — orders_rejected counter increments |
| `test_rejected_order_in_history` | Rejected order recorded in order history |
| `test_no_position_created_on_rejection` | No position created for rejected order |

#### TestFeatureGating

| Test | Description |
|------|-------------|
| `test_stop_order_rejected` | STOP order rejected with ORDER_TYPE_NOT_SUPPORTED |
| `test_stop_limit_order_rejected` | STOP_LIMIT order rejected with ORDER_TYPE_NOT_SUPPORTED |

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

---

### test_live_executor_multi_order.py (8 Tests)

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
| `test_open_close_cycle_completes` | Full open → close cycle with portfolio verification |
| `test_trade_history_after_close` | Closed trade appears in trade history |

#### TestCloseAllRemaining

| Test | Description |
|------|-------------|
| `test_close_all_closes_open_positions` | `close_all_remaining_orders()` closes all positions |
| `test_close_all_on_empty_portfolio` | `close_all_remaining_orders()` handles empty portfolio |

#### TestStatsConsistency

| Test | Description |
|------|-------------|
| `test_sent_equals_executed_plus_rejected` | orders_sent == orders_executed + orders_rejected |

---

### test_live_executor_modify.py (11 Tests)

Limit order modification via broker adapter: successful modify, non-existent order, broker rejection, adapter exceptions, and `get_broker_ref()` reverse lookup. All modification tests use `OrderType.LIMIT` with `price=49000.0` to place orders into `_active_limit_orders` (Hybrid Architecture shadow state — shared sim/live).

LIMIT submit is async post-#319 step 7 (`broker_ref=None` immediately after `open_order`); tests use `await_submit_confirmation` to drain the broker_ref confirmation before calling modify, so the modify path can resolve `order_id → broker_ref`.

#### TestModifyLimitOrderSuccess

| Test | Description |
|------|-------------|
| `test_modify_pending_order_price` | `modify_limit_order()` succeeds for LIMIT order in `_active_limit_orders` |
| `test_modify_pending_order_sl_tp` | Modify SL and TP on pending LIMIT order |
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
| `test_adapter_exception_handled` | Adapter exceptions handled gracefully via processor.modify_order_sync |

#### TestGetBrokerRefReverseLookup

| Test | Description |
|------|-------------|
| `test_get_broker_ref_returns_ref` | `get_broker_ref()` returns broker_ref for known order_id |
| `test_get_broker_ref_unknown_returns_none` | Returns None for unknown order_id |
| `test_get_broker_ref_after_fill_returns_none` | Returns None after order filled (removed from index) |
| `test_get_broker_ref_multiple_orders` | Correct ref returned with multiple tracked orders |

---

### test_async_submit.py (10 Tests) — #321 Regression Coverage

Locks down the SHAPE of the async submit lifecycle introduced by #319 step 6. The other test files cover outcomes; this file specifically asserts the lifecycle itself so a regression to a sync-via-shortcut (which would pass outcome tests) cannot slip through.

Asserts that are unique to this file:
- `result.broker_order_id is None` immediately after `open_order()`
- `pending.broker_ref is None` in the in-flight window between submit and drain
- `pending.broker_ref` confirmed to `MOCK-NNNNNN` after `await_submit_confirmation` drains
- The multi-listener outcome chain fires on the main thread post-drain
- Worker thread joins cleanly on `close_all_remaining_orders()`

#### TestAsyncSubmitInstantFill

| Test | Description |
|------|-------------|
| `test_async_submit_instant_fill_returns_pending` | Initial `open_order()` returns PENDING + `broker_order_id=None` even in INSTANT_FILL mode |
| `test_async_submit_instant_fill_creates_position_after_tick` | Second `feed_tick` drains the inbox: position appears, history has EXECUTED |

#### TestAsyncSubmitRejection

| Test | Description |
|------|-------------|
| `test_async_submit_reject_all_pending_then_rejected` | Initial PENDING, drain delivers REJECTED — counter increments, no position |
| `test_async_reject_triggers_outcome_listener_chain` | OrderGuard registered as outcome listener enters cooldown after async reject (validates the multi-listener chain end-to-end) |

#### TestAsyncSubmitDelayedFill

| Test | Description |
|------|-------------|
| `test_async_submit_delayed_fill_two_tick_lifecycle` | Drain confirms broker_ref while order stays pending; next tick polling fills it (uses `await_submit_confirmation` to isolate the phases) |
| `test_async_submit_broker_ref_set_post_confirmation` | `broker_ref` is None immediately after submit, matches `MOCK-NNNNNN` after drain |

#### TestAsyncSubmitClose

| Test | Description |
|------|-------------|
| `test_async_submit_close_position_async` | Close returns PENDING; next tick drains the close fill and clears the position |

#### TestAsyncSubmitShutdown

| Test | Description |
|------|-------------|
| `test_async_worker_shutdown_during_pending` | Submit, then `close_all_remaining_orders` before drain: clean shutdown, worker thread joined |

#### TestAsyncSubmitTimeout

| Test | Description |
|------|-------------|
| `test_async_submit_timeout_mode` | Submit in TIMEOUT mode stays pending; tick-driven `check_timeouts()` triggers rejection |

#### TestAsyncSubmitMultiple

| Test | Description |
|------|-------------|
| `test_async_submit_multiple_orders` | Three async submits back-to-back, one drain tick, three positions exist |

---

## Running the Tests

```bash
# Live executor suite only
pytest tests/autotrader/live_executor/ -v

# Specific test file
pytest tests/autotrader/live_executor/test_async_submit.py -v

# Specific test class
pytest tests/autotrader/live_executor/test_live_executor_mock.py::TestInstantFill -v
```

---

## Architecture Notes

### Test Design Philosophy

The live executor tests use **direct pipeline validation** rather than scenario execution:

1. MockBrokerAdapter simulates broker behavior (4 configurable modes)
2. MockOrderExecution provides pre-configured executors and tick feeding
3. Each test validates a specific execution path through the pipeline
4. No external dependencies — tests run in under 10 seconds

### Key Data Flow (post-#319)

```
MockOrderExecution
  +-- MockBrokerAdapter (extends AbstractAdapter — native Tier-3)
  |     +-- Tier-3 layers: _build_*_payload / _do_request_* / _parse_*_response × 4 ops
  |     +-- MockExecutionMode controls _do_request_submit / _do_request_query behavior
  |     +-- _resolve_market_fill_price uses last on_tick price (ask for LONG, bid for SHORT)
  |
  +-- LiveTradeExecutor (extends AbstractTradeExecutor)
        +-- LiveRequestProcessor (owns Tier-3 composition)
        |     +-- submit_open_order_async  → SubmitJob → worker → SubmitResponse → inbox
        |     +-- query_order_sync         → Phase-1+2 polling (main thread)
        |     +-- modify_order_sync        → for modify_limit_order
        |     +-- cancel_order_sync        → for cancel_limit_order
        |     +-- drain_inbox              → MARKET internal, LIMIT via _limit_response_hook
        |     +-- _broker_ref_index        → O(1) lookup post-drain
        |
        +-- open_order(MARKET) → register_pending_open(broker_ref=None) + submit_open_order_async
        +-- open_order(LIMIT)  → _active_limit_orders.append(broker_ref=None) + submit_open_order_async(LIMIT)
        +-- close_position()   → register_pending_close(broker_ref=None) + submit_close_order_async
        +-- modify_limit_order → processor.modify_order_sync
        +-- cancel_limit_order → processor.cancel_order_sync
        +-- on_tick(tick):
              Phase 0: processor.drain_inbox()    (worker responses)
              Phase 1: query_order_sync per pending  (MARKET in processor)
              Phase 2: query_order_sync per active   (LIMIT in _active_limit_orders)
        +-- close_all_remaining_orders → cancels active LIMIT, direct-fills open positions, clear_pending, processor.stop_worker
```

### MockBrokerAdapter Modes

| Mode | `_do_request_submit` | `_do_request_query` | `_do_request_modify` | Use Case |
|------|----------------------|---------------------|----------------------|----------|
| `INSTANT_FILL` | Returns FILLED-raw | N/A (no pending) | FILLED if pending | Synchronous-fill brokers |
| `DELAYED_FILL` | Returns PENDING-raw, tracks in `_mock_pending` | Returns FILLED-raw on first query | FILLED if pending | Asynchronous-fill brokers |
| `REJECT_ALL` | Returns REJECTED-raw | N/A | Returns REJECTED-raw | Error handling paths |
| `TIMEOUT` | Returns PENDING-raw | Returns PENDING-raw forever | FILLED if pending | Timeout detection |

### Async Submit Pattern (#319 step 6)

Every `open_order()` and `close_position()` call now returns PENDING with `broker_order_id=None`. The actual broker submission happens asynchronously on a worker thread. The next `feed_tick()` triggers `drain_inbox()` (Phase 0), which:
1. Confirms `broker_ref` if the response is PENDING
2. Calls `mark_filled` + executor hook if the response is FILLED (rare for non-mock brokers)
3. Calls rejection hook + increments `orders_rejected` if the response is REJECTED

For test isolation, `MockOrderExecution` provides two drain helpers:
- `feed_tick(executor, ...)` — flushes outbox, triggers `on_tick` (Phase 0 drain + Phase 1+2 polling)
- `await_submit_confirmation(executor)` — flushes outbox, calls `drain_inbox` only (no `on_tick`, no polling) — used when a test needs broker_ref confirmation without racing the Phase-1 fill
