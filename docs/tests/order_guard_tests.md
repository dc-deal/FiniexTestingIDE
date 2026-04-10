# Order Guard Tests Documentation

## Overview

The order guard test suite validates the OrderGuard spam protection layer — rejection cooldown arming, direction isolation, and the async callback mechanism that bridges latency-delayed outcomes back to the guard.

**Location:** `tests/order_guard/`

---

## Test Structure

Two-level coverage:

```
tests/order_guard/
├── test_order_guard_unit.py          ← Level 1: OrderGuard isolated (no executor)
└── test_order_guard_scenarios.py     ← Level 2: Full backtesting scenarios via DecisionTradingApi
```

### Level 1 — Unit Tests

Direct tests against `OrderGuard` with no executor, no tick loop, no scenario runner. The guard is clock-agnostic — all time-dependent methods take an explicit `now: datetime` parameter, so tests simply pass fixed or advanced timestamps instead of patching `datetime.now()`.

| Class | What it validates |
|-------|-------------------|
| `TestCooldown` | Threshold arming, direction isolation, success reset, expiry, counter accumulation, tick-time anchoring (cooldowns measured in simulated time, not wall-clock) |
| `TestConfigurableThreshold` | Custom `max_consecutive_rejections`, cooldown duration in message |

### Level 2 — Scenario Integration Tests

End-to-end tests that run full backtesting scenarios through the DecisionTradingApi + OrderGuard + TradeSimulator pipeline. Uses `backtesting_margin_stress` decision logic with `trade_sequence` support.

| Class | Scenario Config | What it validates |
|-------|-----------------|-------------------|
| `TestRejectionCooldown` | `order_guard_cooldown_test.json` | Margin rejection arms cooldown, subsequent order blocked as REJECTION_COOLDOWN, stats counted |

---

## Scenario Configs

Located in `configs/scenario_sets/backtesting/`:

### `order_guard_cooldown_test.json`

- **Symbol:** USDJPY on `mt5` (margin mode)
- **Balance:** 80,000 JPY
- **Trade sequence:** 2 LONGs (fill, consume margin), 1 LONG (INSUFFICIENT_MARGIN at fill time), 1 LONG (blocked as REJECTION_COOLDOWN)
- **Guard config:** `max_consecutive_rejections=1` — decouples test from framework default, single rejection arms cooldown
- **`cooldown_seconds=86400`** (1 day). The cooldown is measured in *simulated tick time*, not wall-clock: the scenario runs 2000 ticks across ~10h of data, so ~1 hour of simulated time elapses between the rejecting trade (#2) and the follow-up trade (#3). The long cooldown ensures Trade #3 still sees the armed cooldown regardless of tick spacing.

---

## Key Mechanisms Tested

### Async Callback Path (Level 2)

The cooldown test validates the async callback mechanism end-to-end:

1. Trade #2 submits → `open_order()` returns PENDING (latency pipeline)
2. After latency ticks → `_fill_open_order()` detects INSUFFICIENT_MARGIN
3. `_notify_outcome()` fires → `DecisionTradingApi._on_order_outcome()` → `guard.record_rejection(LONG)`
4. Counter reaches threshold (1) → cooldown armed
5. Trade #3 submits → `guard.validate()` returns REJECTION_COOLDOWN

This is the critical path that was fixed by the callback mechanism — previously `send_order()` saw PENDING and called `record_success()`, resetting the counter.

### Guard Rejection Recording (Level 2)

Guard rejections flow through `AbstractTradeExecutor.record_guard_rejection()` into `_order_history`. Scenario tests verify that:
- Guard rejections appear in order history with correct `RejectionReason`
- All guard rejections carry the `guard_` order ID prefix
- `execution_stats.orders_rejected` includes guard rejections

---

## Fixtures

### Unit Test Fixtures

No shared fixtures — each test creates its own `OrderGuard` instance directly.

### Scenario Test Fixtures

| Fixture | Scope | Description |
|---------|-------|-------------|
| `cooldown_tick_loop` | module | Runs `order_guard_cooldown_test` scenario once |
| `cooldown_order_history` | module | Extracts order history from tick loop results |
| `cooldown_execution_stats` | module | Extracts ExecutionStats from tick loop results |

All scenario fixtures use `run_scenario()` from `tests/shared/fixture_helpers.py`.
