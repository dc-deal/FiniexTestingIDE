# Live Executor Tests Documentation

## Overview

The live executor test suite validates the LiveTradeExecutor, LiveRequestProcessor, and MockBrokerAdapter pipeline. All tests run against MockBrokerAdapter — no network, no config files, no tick data files required.

**Test Configuration:** MockBrokerAdapter with real Kraken BTCUSD symbol specification
- Symbol: BTCUSD
- Account Currency: USD
- Initial Balance: 10,000 USD
- Execution Modes: instant_fill, delayed_fill, reject_all, timeout

**Total Tests:** 87

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
├── test_async_submit.py                ← Level 5: Async submit lifecycle regressions (#321)
├── test_async_modify.py                ← Level 6: Async modify lifecycle regressions (#318)
├── test_async_cancel.py                ← Level 7: Async cancel lifecycle regressions (#318)
├── test_broker_trade_records.py        ← Level 8: BrokerTrade aggregation + async trades_query (#326)
└── test_polling_cadence.py             ← Level 9: Heartbeat, async polling, throttle, in-flight (#320)
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
| `test_broker_rejects_modify` | Async (#318): initial PENDING accept; broker rejection arrives via drain on next tick; `orders_rejected` counter increments |

#### TestModifyLimitOrderAdapterException

| Test | Description |
|------|-------------|
| `test_adapter_exception_handled` | Async (#318): exception raised in worker thread, surfaced as REJECTED via drain; `orders_rejected` counter increments |

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
- `result.position_id is None` immediately after `open_order()`
- `pending.broker_ref is None` in the in-flight window between submit and drain
- `pending.broker_ref` confirmed to `MOCK-NNNNNN` after `await_submit_confirmation` drains
- The multi-listener outcome chain fires on the main thread post-drain
- Worker thread joins cleanly on `close_all_remaining_orders()`

#### TestAsyncSubmitInstantFill

| Test | Description |
|------|-------------|
| `test_async_submit_instant_fill_returns_pending` | Initial `open_order()` returns PENDING + `position_id=None` even in INSTANT_FILL mode |
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

### test_async_modify.py (11 Tests) — #318 Modify Regression Coverage

Locks down the shape of the async modify lifecycle introduced by #318:
- `modify_limit_order` returns `success=True, status=PENDING` immediately
- `target.in_flight_operation = PENDING_MODIFY` during the in-flight window
- `drain_inbox` applies the modification on next tick (entry_price, SL, TP)
- broker_ref swap (defensive cancel-replace path; Kraken `AmendOrder` keeps the ref) is handled in drain
- Busy / not-confirmed / not-found / unsupported-capability reject paths

Uses `await_submit_confirmation` for drain isolation (no Phase-2 polling that would fill the order in DELAYED_FILL mode).

#### TestModifyLimitOrderAsyncLifecycle

| Test | Description |
|------|-------------|
| `test_modify_returns_pending_initially` | Returns `success=True, status=PENDING, order_id=order_id` |
| `test_in_flight_operation_set_during_window` | `target.in_flight_operation == PENDING_MODIFY`, `pending_modification.new_price` populated |
| `test_modification_applied_after_drain` | After `await_submit_confirmation`: entry_price + SL + TP reflect new values |
| `test_in_flight_clears_after_drain` | After drain: `in_flight_operation == NONE`, `pending_modification is None` |

#### TestModifyLimitOrderBusy

| Test | Description |
|------|-------------|
| `test_second_modify_returns_busy` | Second modify on PENDING_MODIFY order → OPERATION_BUSY |
| `test_modify_during_pending_cancel_returns_busy` | Modify on PENDING_CANCEL order → OPERATION_BUSY |

#### TestModifyLimitOrderNotConfirmed

| Test | Description |
|------|-------------|
| `test_modify_before_broker_ref_confirmed` | Modify on order with broker_ref=None → ORDER_NOT_CONFIRMED (Option A) |

#### TestModifyLimitOrderNotFound

| Test | Description |
|------|-------------|
| `test_modify_nonexistent_order` | Unknown order_id → LIMIT_ORDER_NOT_FOUND |

#### TestModifyStopOrderCapabilityGate

| Test | Description |
|------|-------------|
| `test_modify_stop_order_rejected_for_kraken_profile` | Mock declares stop_orders=False → ORDER_TYPE_NOT_SUPPORTED |

#### TestModifyLimitOrderHasInFlight

| Test | Description |
|------|-------------|
| `test_has_in_flight_operation_during_window` | `has_in_flight_operation(order_id)` returns True between schedule and drain |
| `test_has_in_flight_operation_clears_after_drain` | Returns False after drain — and verifies order is still in active list (cleared, not filled) |

---

### test_async_cancel.py (9 Tests) — #318 Cancel Regression Coverage

Locks down the shape of the async cancel lifecycle:
- `cancel_limit_order` returns True (scheduled) immediately
- `target.in_flight_operation = PENDING_CANCEL` during the in-flight window
- `drain_inbox` removes the order from `_active_limit_orders` on success
- Busy / not-confirmed / not-found / unsupported-capability reject paths

#### TestCancelLimitOrderAsyncLifecycle

| Test | Description |
|------|-------------|
| `test_cancel_returns_true_when_scheduled` | Returns True for valid, confirmed, idle order |
| `test_in_flight_operation_set_during_window` | `target.in_flight_operation == PENDING_CANCEL` |
| `test_order_removed_from_active_after_drain` | After `feed_tick`: order removed from `_active_limit_orders` |

#### TestCancelLimitOrderBusy

| Test | Description |
|------|-------------|
| `test_second_cancel_returns_false` | Second cancel on PENDING_CANCEL order → False (busy) |
| `test_cancel_during_pending_modify_returns_false` | Cancel on PENDING_MODIFY order → False (busy) |

#### TestCancelLimitOrderNotConfirmed

| Test | Description |
|------|-------------|
| `test_cancel_before_broker_ref_confirmed` | Cancel on order with broker_ref=None → False (Option A) |

#### TestCancelLimitOrderNotFound

| Test | Description |
|------|-------------|
| `test_cancel_nonexistent_order` | Unknown order_id → False |

#### TestCancelStopOrderCapabilityGate

| Test | Description |
|------|-------------|
| `test_cancel_stop_order_returns_false_for_kraken_profile` | Mock declares stop_orders=False → False |

#### TestCancelHasInFlight

| Test | Description |
|------|-------------|
| `test_has_in_flight_operation_after_cancel_schedule` | `has_in_flight_operation(order_id)` returns True after cancel schedule |

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

Every `open_order()` and `close_position()` call now returns PENDING with `position_id=None`. The actual broker submission happens asynchronously on a worker thread. The next `feed_tick()` triggers `drain_inbox()` (Phase 0), which:
1. Confirms `broker_ref` if the response is PENDING
2. Calls `mark_filled` + executor hook if the response is FILLED (rare for non-mock brokers)
3. Calls rejection hook + increments `orders_rejected` if the response is REJECTED

For test isolation, `MockOrderExecution` provides two drain helpers:
- `feed_tick(executor, ...)` — flushes outbox, triggers `on_tick` (Phase 0 drain + Phase 1+2 polling)
- `await_submit_confirmation(executor)` — flushes outbox, calls `drain_inbox` only (no `on_tick`, no polling) — used when a test needs broker_ref confirmation without racing the Phase-1 fill

### test_broker_trade_records.py (9 Tests) — #326 BrokerTrade Layer

Validates the order ↔ executions pairing model: `BrokerTrade` aggregation on `PendingOrder`, the polling-path synthesis baseline, the async `submit_trades_query_async` roundtrip via worker + drain, the stale-broker_ref guard, and the `trade_level_reporting` capability flag.

#### TestPendingOrderAppendTrade

| Test | Description |
|---|---|
| `test_empty_pending_has_zero_cumulatives` | Default `PendingOrder` has empty `trades` and zero `cumulative_*` |
| `test_single_trade_sets_cumulatives` | One `append_trade` populates cumulative_filled_lots / fee / avg_price |
| `test_three_trades_weighted_avg_price` | Three trades at different prices → weighted average price computed correctly |

#### TestPollingPathSynthesizesTrade

| Test | Description |
|---|---|
| `test_market_fill_creates_one_synthetic_trade` | MARKET fill triggers shared `_synthesize_pending_trade` once; position is created in portfolio |

#### TestTradesQueryAsyncRoundtrip

| Test | Description |
|---|---|
| `test_submit_trades_query_async_returns_records_via_drain` | Enqueue `TradesQueryJob` → worker dispatches → `TradesQueryResponse` arrives via `drain_inbox` → `trades_response_hook` fires with parsed `List[BrokerTrade]` |
| `test_multi_trade_mock_yields_n_records` | `trades_per_fill=3` on the mock → drain delivers 3 records with total volume preserved |
| `test_unknown_broker_ref_yields_empty_trades` | Querying an unrecorded broker_ref returns success=True with empty trades list (graceful) |

#### TestStaleResponseGuard

| Test | Description |
|---|---|
| `test_stale_trades_response_does_not_remove_pending` | Drain handler discards response when `response.broker_ref != pending.broker_ref` — order stays in `_active_limit_orders`, no trades appended |

#### TestCapabilityFlag

| Test | Description |
|---|---|
| `test_mock_reports_trade_level_capability` | `MockBrokerAdapter.get_order_capabilities().trade_level_reporting is True` |

### test_polling_cadence.py (12 Tests) — #320 Live Polling Cadence

Validates the three coordinated fixes introduced by #320: side-effect-free `heartbeat()` for idle ticks, async per-order polling via the worker thread, and the in-flight guard + wall-clock throttle on `_process_active_orders`. All tests exercise the LIMIT-order polling path; MARKET stays sync (out of scope).

#### TestHeartbeat

| Test | Description |
|---|---|
| `test_heartbeat_drains_inbox_without_tick_state` | `heartbeat()` drains async responses without bumping `_tick_counter` or replacing `_current_tick` |
| `test_heartbeat_processes_timeouts` | A pending order whose `timeout_at` is in the past becomes REJECTED on the next `heartbeat()` |
| `test_heartbeat_sim_is_noop` | `TradeSimulator` inherits the default no-op `heartbeat()` — no errors, no state mutation |

#### TestThrottle

| Test | Description |
|---|---|
| `test_active_limit_polled_at_most_once_per_interval` | 50 scheduler calls within `poll_interval_ms=1000` → exactly 1 dispatch |
| `test_throttle_interval_configurable` | `poll_interval_ms=200` → second dispatch only after sleeping past the window |
| `test_throttle_uses_wall_clock_not_tick_time` | A 7-day-future tick must not bypass the wall-clock throttle gate |

#### TestInFlightGuard

| Test | Description |
|---|---|
| `test_in_flight_query_blocks_concurrent_dispatch` | `pending.in_flight_query=True` → scheduler skips silently, no second dispatch |
| `test_in_flight_cleared_on_pending_response` | PENDING response clears `in_flight_query`; order stays in `_active_limit_orders` |
| `test_in_flight_cleared_on_filled` | FILLED response clears `in_flight_query` as a side effect; order removed, position opened |
| `test_in_flight_cleared_on_stale_response` | Stale broker_ref response clears `in_flight_query` but leaves state untouched |

#### TestStaleResponseGuard

| Test | Description |
|---|---|
| `test_stale_query_after_modify_discarded` | After a modify swaps `broker_ref` A→B, a FILLED response against A does NOT remove the order |

#### TestPartialFillPreservedBehavior

| Test | Description |
|---|---|
| `test_partially_filled_keeps_polling` | PARTIALLY_FILLED → no state mutation, order stays active, `in_flight_query` cleared. Per-execution accumulation lands with #326's async `trades_query`. |

---

### test_drift_auditor.py (17 Tests) — #327 Drift Audit + #340 Slippage

Validates the read-only drift telemetry pipeline established by #327: outcome-listener captures synthetic snapshot, async trades-query roundtrip, multi-consumer fan-out, comparison + counter classification, currency-aware FEE skip, coexistence with OrderGuard, leak-free response handling, and consumer-exception isolation. The SLIPPAGE channel added by #340 reuses the same pipeline pattern with a fourth `DriftType` comparison branch.

Uses a `_FakeExecutor` stub that records listener / consumer registrations and lets tests drive `fire_outcome()` / `fire_trades_response()` directly — no worker thread, no real adapter.

#### TestDisabledAudit

| Test | Description |
|---|---|
| `test_disabled_audit_is_noop` | `DriftAuditConfig(enabled=False)` → listener fires but produces no `submit_trades_query_async` call |

#### TestThresholdBehaviour

| Test | Description |
|---|---|
| `test_no_drift_within_threshold` | Synthetic == broker → `total_orders_audited=1`, all event counters stay at 0 |
| `test_fee_drift_above_threshold_logged` | Local fee 5 % below broker → `fee_events=1`, FEE record marked `threshold_exceeded=True` |

#### TestPartialFill

| Test | Description |
|---|---|
| `test_volume_drift_partial_fill` | Broker filled 0.05 of requested 0.10 → 50 % VOLUME drift, `volume_events=1` |

#### TestPriceDriftStructural

| Test | Description |
|---|---|
| `test_price_drift_marked_structural` | Broker avg-price 5 % off local → PRICE record carries `is_structural=True`, counter increments above 1 % threshold |

#### TestDryRunSkipped

| Test | Description |
|---|---|
| `test_dryrun_orders_skipped` | `pending.broker_ref` starting `DRYRUN-` → no trades-query triggered, no snapshot stored |

#### TestFeeCurrencyMismatch

| Test | Description |
|---|---|
| `test_fee_currency_mismatch_skips_comparison` | Local USD vs. broker EUR → FEE compare skipped (warning logged), VOLUME and PRICE still recorded |

#### TestCoexistenceWithOrderGuard

| Test | Description |
|---|---|
| `test_drift_auditor_coexists_with_order_guard` | Both DriftAuditor and a guard-style listener register on the executor → both fire on outcome (#319 multi-listener regression guard) |

#### TestFailedTradesResponseNoLeak

| Test | Description |
|---|---|
| `test_failed_trades_response_no_leak` | `response.success=False` → snapshot is still popped from `_pending_audits` (no leak, Risk 4 regression guard) |
| `test_shutdown_clears_unfinished_audits` | `shutdown()` clears any unfinished entries and emits the final summary log line |

#### TestConsumerExceptionIsolation

| Test | Description |
|---|---|
| `test_consumer_exception_isolated` | A bad consumer that raises does NOT prevent subsequent consumers from running (Risk 2 regression guard — validates the `try/except` fan-out pattern in `_handle_trades_response`) |

#### TestSlippageAudit — #340

Validates the fourth audit channel: trade-channel tick mid-price captured at submission vs. broker's actual fill price. Always structural (slippage is market reality, not a bug). Threshold-gated counter, max-tracked magnitude. Action-agnostic — fires for both open and close orders. Snapshots with `submission_tick_mid_price=None` (synthetic cleanup pendings, cold-start paths) are skipped gracefully.

| Test | Description |
|---|---|
| `test_slippage_recorded_when_tick_differs_from_fill` | Sub-threshold case (Pilot-Run baseline $2110.91 → $2110.95 ≈ 0.0019 %) → SLIPPAGE record present, `threshold_exceeded=False`, counter stays 0, max-tracker reflects magnitude |
| `test_slippage_above_threshold_increments_counter` | ~0.94 % delta → `slippage_events=1`, `is_structural=True`, threshold flag set |
| `test_missing_submission_tick_gracefully_skips` | `submission_tick_mid_price=None` → no SLIPPAGE record, no exception, other dimensions still ran (no leak) |
| `test_slippage_always_marked_structural` | Every SLIPPAGE record carries `is_structural=True` regardless of threshold breach (sub + over case) |
| `test_slippage_zero_when_tick_matches_fill` | Exact match → `relative_delta_pct=0`, counter stays 0, record still appended as evidence-of-compare |
| `test_slippage_captured_on_close_order` | `PendingOrderAction.CLOSE` pending with submission_tick set → SLIPPAGE record produced (action-agnostic compare verification — partial-close slippage path) |
