# Active Order Display Tests Documentation

## Overview

The active order display test suite validates that unresolved pending orders (limit and stop) are correctly reported in `PendingOrderStats.active_limit_orders` and `active_stop_orders` at scenario end.

**Test Configuration:** `backtesting/limit_stop_order_mock_scenario_test.json`
- Symbol: GBPUSD
- 2 scenarios: 1 limit scenario, 1 stop scenario
- 500 ticks each — fast execution (~5 seconds total)

**Total Tests:** 14

**Location:** `tests/simulation/active_order_display/`

---

## Test Structure

```
tests/
├── shared/
│   ├── fixture_helpers.py               ← extract_pending_stats() used here
│   └── shared_active_order_display.py   ← Reusable test classes
├── active_order_display/
│   ├── conftest.py                      ← Dual-scenario fixtures (limit + stop)
│   ├── test_active_limit_orders.py      ← Imports TestActiveLimitOrdersReported
│   └── test_active_stop_orders.py       ← Imports TestActiveStopOrdersReported
```

---

## Fixtures (conftest.py)

The conftest runs one batch (both scenarios), then extracts per-scenario fixtures independently.

| Fixture | Scope | Description |
|---------|-------|-------------|
| `batch_execution_summary` | session | Runs both scenarios once per session |
| `process_result_limit` | session | ProcessResult for scenario 0 (active_limit_display) |
| `tick_loop_results_limit` | session | Tick loop results for limit scenario |
| `pending_stats_limit` | session | PendingOrderStats for limit scenario |
| `process_result_stop` | session | ProcessResult for scenario 1 (active_stop_display) |
| `tick_loop_results_stop` | session | Tick loop results for stop scenario |
| `pending_stats_stop` | session | PendingOrderStats for stop scenario |

---

## Test Classes

### TestActiveLimitOrdersReported (7 tests)
Uses `pending_stats_limit` fixture. Validates scenario 0: LONG LIMIT at price 0.5000, SL 0.4900, TP 0.5200.

| Test | Validates |
|------|-----------|
| `test_active_limit_orders_populated` | `active_limit_orders` has exactly 1 entry |
| `test_active_limit_order_direction` | Entry direction is LONG |
| `test_active_limit_order_type` | Entry order_type is LIMIT |
| `test_active_limit_order_entry_price` | Entry `entry_price == 0.5000` |
| `test_active_limit_order_stop_loss` | Entry `stop_loss == 0.4900` |
| `test_active_limit_order_take_profit` | Entry `take_profit == 0.5200` |
| `test_active_stop_orders_empty` | `active_stop_orders` is empty (no stop orders placed) |

### TestActiveStopOrdersReported (7 tests)
Uses `pending_stats_stop` fixture. Validates scenario 1: LONG STOP at stop_price 5.0000, SL 4.9500, TP 5.1000.

| Test | Validates |
|------|-----------|
| `test_active_stop_orders_populated` | `active_stop_orders` has exactly 1 entry |
| `test_active_stop_order_direction` | Entry direction is LONG |
| `test_active_stop_order_type` | Entry order_type is STOP |
| `test_active_stop_order_entry_price` | Entry `entry_price == 5.0000` |
| `test_active_stop_order_stop_loss` | Entry `stop_loss == 4.9500` |
| `test_active_stop_order_take_profit` | Entry `take_profit == 5.1000` |
| `test_active_limit_orders_empty` | `active_limit_orders` is empty (no limit orders placed) |

---

## Scenario Design

Both scenarios use intentionally unreachable prices to ensure the order stays active for the full 500 ticks:

**Scenario 0 (active_limit_display):**
- LONG LIMIT at price 0.5000, SL 0.4900, TP 0.5200 — far below GBPUSD market price (~1.30)
- Order is sent at tick 10, never fills, remains in `_active_limit_orders`
- At scenario end: `active_limit_orders` has 1 snapshot with SL/TP, `active_stop_orders` is empty

**Scenario 1 (active_stop_display):**
- LONG STOP at stop_price 5.0000, SL 4.9500, TP 5.1000 — far above GBPUSD market price (~1.30)
- Order is sent at tick 10, never triggers, remains in `_active_stop_orders`
- At scenario end: `active_stop_orders` has 1 snapshot with SL/TP, `active_limit_orders` is empty

---

## Key Data Flow

```
trade_simulator._active_limit_orders / _active_stop_orders
  └→ get_pending_stats()
       └→ PendingOrderStats.active_limit_orders / active_stop_orders: List[ActiveOrderSnapshot]
            └→ ProcessTickLoopResult.pending_stats
                 └→ pending_stats_limit / pending_stats_stop fixtures
```

`ActiveOrderSnapshot` contains: `order_id`, `order_type`, `symbol`, `direction`, `lots`, `entry_price`, `limit_price` (STOP_LIMIT only), `stop_loss`, `take_profit`.

---

## Running the Tests

```bash
pytest tests/simulation/active_order_display/ -v
```

**VS Code:** Use launch configuration `🧩 Pytest: Active Order Display (All)`.
