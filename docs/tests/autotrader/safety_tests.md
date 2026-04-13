# Safety Circuit Breaker Tests Documentation

## Overview

The safety circuit breaker test suite validates the account-level protection mechanism in the AutoTrader tick loop. It covers the equity-based evaluation for spot mode, balance-based evaluation for margin mode, the config split (`min_balance` / `min_equity`), and the live display state tracking.

**AutoTrader pipeline only.** The Safety Circuit Breaker does not exist in backtesting — simulation intentionally shows full consequences of an algorithm's behavior, including worst-case drawdowns. See [safety_circuit_breaker_architecture.md](../../architecture/safety_circuit_breaker_architecture.md) for design rationale.

**Location:** `tests/autotrader/safety/`

---

## Test Structure

Two-level coverage:

```
tests/autotrader/safety/
├── test_safety_unit.py                  ← Level 1: _check_safety isolated (stub, no tick loop)
├── test_safety_integration.py           ← Level 2: Spot mock sessions (kraken_spot/BTCUSD)
└── test_margin_safety_integration.py    ← Level 2: Margin mock sessions (mt5/EURUSD)
```

### Level 1 — Unit Tests (22 tests)

Direct tests against `_check_safety` using a lightweight stub that mirrors the instance attributes the method reads/writes. No executor, no tick source, no bar rendering — pure logic validation.

| Class | What it validates |
|-------|-------------------|
| `TestSpotMinEquity` | `min_equity` triggers/clears in spot mode, `min_balance` inert in spot mode |
| `TestMarginMinBalance` | `min_balance` triggers/clears in margin mode, `min_equity` inert in margin mode |
| `TestDrawdown` | Equity-based drawdown (spot), balance-based drawdown (margin), no phantom drawdown after BUY (#270 core fix), threshold crossing, recovery |
| `TestCombinedConditions` | OR-combined: both fire, only min fires, only drawdown fires |
| `TestDisabled` | Zero thresholds disabled, `enabled=False` skips all checks |
| `TestDisplayState` | `_safety_current_value` stored, `_safety_drawdown_pct` stored, drawdown floored at 0% (profit case) |

### Level 2 — Integration Tests: Spot (8 tests)

End-to-end tests that run full AutoTrader mock sessions in **spot mode** with overridden safety configs. Uses `btcusd_mock_safety.json` (kraken_spot/BTCUSD, 15K ticks, display off, INSTANT_FILL mock adapter, `simple_consensus` decision logic). Three session scenarios, each run once and shared across all tests in the module.

| Class | Safety Config | What it validates |
|-------|--------------|-------------------|
| `TestSpotSafetyNoFalsePositive` | `min_equity=100, max_drawdown_pct=50` | No false trigger, trades execute normally, no safety warnings |
| `TestSpotSafetyTriggers` | `min_equity=9999, max_drawdown_pct=0.01` | Circuit breaker triggers (spread cost alone exceeds threshold), session completes (soft stop) |
| `TestSafetyDisabledNoInterference` | `enabled=False` | Trades execute, no safety warnings, no interference |

### Level 2 — Integration Tests: Margin (10 tests)

End-to-end tests that run full AutoTrader mock sessions in **margin mode** with overridden safety configs. Uses `margin_safety_test.json` (mt5/EURUSD, 15K ticks, display off, INSTANT_FILL mock adapter, `backtesting_margin_stress` with deterministic `trade_sequence`). Three session scenarios.

| Class | Safety Config | What it validates |
|-------|--------------|-------------------|
| `TestMarginSafetyNoFalsePositive` | `min_balance=100, max_drawdown_pct=50` | No false trigger, trades execute normally, no safety warnings |
| `TestMarginSafetyTriggers` | `min_balance=10001, max_drawdown_pct=50` | Circuit breaker triggers (`min_balance` above initial), warning contains "min_balance" (not "min_equity"), no trades (all blocked) |
| `TestMarginSafetyDisabledNoInterference` | `enabled=False` | Trades execute, no safety warnings, no interference |

**Key difference from spot:** In margin mode, balance only changes when trades are **closed** (not on open). The trigger session uses `min_balance=10001` (above the 10000 initial balance) to trigger immediately, validating that the margin path correctly checks `min_balance`.

---

## Key Mechanisms Tested

### Phantom Drawdown Prevention (#270 Core Fix)

The central test case (`test_spot_no_phantom_drawdown_after_buy`) validates:
1. Initial equity = 12.49 USD
2. After BUY: equity = 12.48 (spread cost only)
3. Drawdown < 1% — not the phantom 17.2% from raw balance

This is the #270 fix: `_check_safety` receives equity (balance + held asset value) in spot mode, not raw balance.

### Config Split: `min_balance` vs `min_equity`

Each trading model uses its own min-threshold field:
- Spot: `min_equity` active, `min_balance` inert
- Margin: `min_balance` active, `min_equity` inert

Unit tests verify cross-inertness: setting the "wrong" field to an extreme value does not trigger safety in the other mode.

### Soft Stop Behavior

Integration test `TestSpotSafetyTriggers` confirms that aggressive thresholds trigger the breaker but the session still completes normally (`shutdown_mode == 'normal'`). Safety blocks entries — it does not crash or force-close positions.

---

## Fixtures

### Unit Test Fixtures

No shared fixtures. Each test creates its own `_SafetyStub` via `_make_stub()`. The stub binds `AutotraderTickLoop._check_safety` as an unbound method call — the logic under test is exactly the production code.

### Integration Test Fixtures — Spot

| Fixture | Scope | Description |
|---------|-------|-------------|
| `safe_session` | module | Mock session with generous safety thresholds |
| `trigger_session` | module | Mock session with aggressive thresholds (forces trigger) |
| `disabled_session` | module | Mock session with safety disabled |

All fixtures use `_run_with_safety()` which loads `btcusd_mock_safety.json`, overrides `config.safety`, runs the session, and cleans up the log directory.

### Integration Test Fixtures — Margin

| Fixture | Scope | Description |
|---------|-------|-------------|
| `margin_safe_session` | module | Margin session with generous safety thresholds |
| `margin_trigger_session` | module | Margin session with `min_balance` above initial (forces trigger) |
| `margin_disabled_session` | module | Margin session with safety disabled |

All fixtures use `_run_with_margin_safety()` which loads `margin_safety_test.json`, overrides `config.safety`, runs the session, and cleans up the log directory.

---

## Performance

- Unit tests: ~0.1s (no I/O, no tick processing)
- Spot integration tests: ~9s total (3 sessions x 15K ticks each)
- Margin integration tests: ~10s total (3 sessions x 15K ticks each)
- Total suite: ~19s
