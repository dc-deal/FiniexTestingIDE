# Spot SELL Tests Documentation

## Overview

The spot sell test suite validates that SELL signals on spot markets correctly flow through the full execution pipeline without being blocked by the OrderGuard. This was previously impossible because the guard hard-blocked SHORT (SELL) on spot — the OrderSide/OrderDirection split removed that barrier.

**Location:** `tests/spot_trading/`

---

## Test Structure

```
tests/spot_trading/
└── test_spot_sell_scenarios.py     ← Full backtesting scenario on kraken_spot
```

### Scenario Tests

End-to-end tests using `backtesting_margin_stress` decision logic with `trade_sequence` on kraken_spot (ETHUSD).

| Class | What it validates |
|-------|-------------------|
| `TestSpotBuyExecutes` | BUY on spot executes normally (baseline) |
| `TestSpotSellWithBalance` | SELL on spot with held base currency executes (not guard-rejected) |
| `TestSpotSellInsufficientBalance` | SELL without base balance → INSUFFICIENT_FUNDS rejection |

---

## Scenario Config

Located at `configs/scenario_sets/backtesting/spot_sell_test.json`:

- **Symbol:** ETHUSD on `kraken_spot` (spot mode)
- **Balance:** 10,000 USD
- **Trade sequence:**
  1. LONG 0.01 ETH at tick 100 (acquires base currency)
  2. SHORT 0.01 ETH at tick 800 (sells held base currency — the key test)
  3. SHORT 1.0 ETH at tick 1500 (INSUFFICIENT_FUNDS — no base balance)

---

## Fixtures

| Fixture | Scope | Description |
|---------|-------|-------------|
| `spot_sell_tick_loop` | module | Runs `spot_sell_test` scenario once |
| `spot_sell_order_history` | module | Extracts order history from tick loop results |
| `spot_sell_execution_stats` | module | Extracts ExecutionStats from tick loop results |

All fixtures use `run_scenario()` from `tests/shared/fixture_helpers.py`.
