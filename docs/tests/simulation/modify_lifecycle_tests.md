# Modify Lifecycle Tests Documentation

## Overview

The modify_lifecycle test suite validates the async modify/cancel pattern introduced by #318 on the **simulation side**. Unlike most other simulation test suites which run full scenarios via `execute_tick_loop`, this suite instantiates `TradeSimulator` directly and drives it with controlled msc-tagged ticks. The focus is on the **shape of the modify/cancel lifecycle** — scheduling, in-flight state, resolve-on-next-tick — not on full P&L correctness.

**Test Configuration:** Direct `TradeSimulator` instantiation via fixture
- Adapter: `MockBrokerAdapter(mode=INSTANT_FILL)` with zero inbound latency
- Account: 10,000 USD initial balance
- Symbol: BTCUSD (Mock built-in spec)

**Total Tests:** 17

**Location:** `tests/simulation/modify_lifecycle/`

---

## Test Structure

### Direct Executor Instantiation (no scenario execution)

The suite uses direct `TradeSimulator` instantiation rather than scenario-based execution. This makes each test sharply focused on a single lifecycle step (schedule, resolve, cleanup) without scenario loading overhead.

```
tests/simulation/modify_lifecycle/
├── __init__.py
├── conftest.py                          ← sim_executor fixture + feed_sim_tick helper
├── test_modify_pending_lifecycle.py     ← modify_limit_order + modify_position
└── test_cancel_pending_lifecycle.py     ← cancel_limit_order
```

Helper functions in `conftest.py`:

| Helper | Purpose |
|---|---|
| `sim_executor` (fixture) | TradeSimulator with zero-latency INSTANT_FILL Mock |
| `feed_sim_tick(executor, msc, bid, ask, symbol)` | Direct tick feed with controlled msc — advances sim clock |

Each test typically does:
1. Submit a LIMIT order via `executor.open_order(...)`
2. Feed one tick to drain latency → order in `_active_limit_orders`
3. Call `modify_limit_order(...)` or `cancel_limit_order(...)`
4. (Optional) Feed next tick to drive Phase 0 resolve
5. Assert lifecycle state (in_flight_operation flag, applied modification, removed from list, etc.)

---

## Test Files

### test_modify_pending_lifecycle.py (10 Tests)

Validates the sim modify lifecycle: scheduling sets `in_flight_operation = PENDING_MODIFY`, Phase 0 of next-tick processing applies the modification to entry_price / order_kwargs, in-flight state clears. Capability gates for stop-order modify and position modify.

#### TestModifyLimitOrderAsyncLifecycle

| Test | Description |
|---|---|
| `test_modify_returns_pending_initially` | `modify_limit_order` returns `success=True, status=PENDING, order_id=order_id` |
| `test_in_flight_operation_set_during_window` | After scheduling, `target.in_flight_operation == PENDING_MODIFY`, `pending_modification.new_price` set |
| `test_modification_applied_after_next_tick` | Next `feed_sim_tick` applies new entry_price + SL + TP to the target PendingOrder |
| `test_in_flight_clears_after_resolve` | After resolve, `in_flight_operation == NONE`, `pending_modification is None` |

#### TestModifyLimitOrderBusy

| Test | Description |
|---|---|
| `test_second_modify_returns_busy` | Second modify while first is in-flight → `OPERATION_BUSY` rejection |
| `test_modify_during_pending_cancel_returns_busy` | Modify on order with PENDING_CANCEL → `OPERATION_BUSY` |

#### TestModifyLimitOrderNotFound

| Test | Description |
|---|---|
| `test_modify_nonexistent_order` | Unknown order_id → `LIMIT_ORDER_NOT_FOUND` |

#### TestModifyStopOrderCapabilityGate

| Test | Description |
|---|---|
| `test_modify_stop_order_rejected_for_kraken_profile` | Mock declares `stop_orders=False` → `ORDER_TYPE_NOT_SUPPORTED` |

#### TestModifyPositionCapabilityGate

| Test | Description |
|---|---|
| `test_modify_position_sync_fallback_for_kraken_caps` | `native_position_sl_tp=False` → instant `portfolio.modify_position` (sync fallback, `status=SUCCESS`, no entry in `_pending_position_modifications`) |
| `test_modify_position_async_path_with_mt5_caps` | Monkey-patched `native_position_sl_tp=True` → async pending pattern: schedule registers in `_pending_position_modifications`, next tick applies SL/TP via portfolio.modify_position, tracker drained |

---

### test_cancel_pending_lifecycle.py (7 Tests)

Validates the sim cancel lifecycle: scheduling sets `in_flight_operation = PENDING_CANCEL`, Phase 0 of next-tick processing removes the order from `_active_limit_orders` / `_active_stop_orders`.

#### TestCancelLimitOrderAsyncLifecycle

| Test | Description |
|---|---|
| `test_cancel_returns_true_when_scheduled` | `cancel_limit_order` returns True for a valid, resting, idle order |
| `test_in_flight_operation_set_during_window` | After scheduling, `target.in_flight_operation == PENDING_CANCEL`, `cancel_apply_at_msc` set |
| `test_order_removed_from_active_after_resolve` | Next `feed_sim_tick` removes the order from `_active_limit_orders` |

#### TestCancelLimitOrderBusy

| Test | Description |
|---|---|
| `test_second_cancel_returns_false` | Second cancel while first is in-flight → returns False (busy) |
| `test_cancel_during_pending_modify_returns_false` | Cancel on order with PENDING_MODIFY → False (busy) |

#### TestCancelLimitOrderNotFound

| Test | Description |
|---|---|
| `test_cancel_nonexistent_order` | Unknown order_id → False |

#### TestCancelStopOrderCapabilityGate

| Test | Description |
|---|---|
| `test_cancel_stop_order_returns_false_for_kraken_profile` | Mock declares `stop_orders=False` → returns False |

---

## Running the Tests

```bash
# Modify lifecycle suite only
pytest tests/simulation/modify_lifecycle/ -v

# Specific test file
pytest tests/simulation/modify_lifecycle/test_modify_pending_lifecycle.py -v

# Specific test class
pytest tests/simulation/modify_lifecycle/test_modify_pending_lifecycle.py::TestModifyLimitOrderAsyncLifecycle -v
```

Launch.json entry: `🧩 Pytest: Sim Modify Lifecycle (#318)`

---

## Architecture Notes

### Test Design Philosophy

Unlike scenario-based simulation tests (`baseline`, `sltp_limit_validation`, etc.), this suite uses **direct executor instantiation**:

1. Each test creates a fresh `TradeSimulator` via the `sim_executor` fixture
2. `feed_sim_tick` advances the sim clock with explicit msc values
3. Tests assert lifecycle state directly via `executor._active_limit_orders`, `pending.in_flight_operation`, etc.
4. No scenario config, no `execute_tick_loop`, no decision logic

This makes tests sharply focused (one lifecycle step per test) and fast (the whole suite runs in <1s).

### Key Lifecycle Pattern

```
algo:
  modify_limit_order(order_id, new_price=...)
        │
        ▼ scheduling (current tick's msc)
TradeSimulator:
  pending.in_flight_operation = PENDING_MODIFY
  pending.pending_modification = ModificationRequest(apply_at_msc=current_msc + 1)
        │
        ▼ next tick (Phase 0)
TradeSimulator._resolve_pending_operations:
  if pending.pending_modification.apply_at_msc <= tick.msc:
    apply pending_modification to entry_price + order_kwargs
    clear in_flight_operation
```

The `_modify_cancel_delay_msc = 1` default means resolution happens on the very next tick. Real broker-side latency modeling (seeded delays for modify/cancel) is a future extension — out of scope for #318.

### Sim/Live Parity

The algo-facing contract is identical to the live pipeline:
- Same `ModificationResult` shape (success, status, rejection_reason)
- Same `in_flight_operation` transitions
- Same `has_in_flight_operation(order_id)` semantics

The resolution mechanism differs (sim: msc-clock-based on next tick / live: worker-thread → drain_inbox). The parity is verified in `tests/parity/test_modify_cancel_parity.py`.
