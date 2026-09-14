# Safety Circuit Breaker Tests Documentation

## Overview

The safety circuit breaker test suite validates the account-level protection mechanism in the
AutoTrader tick loop. It covers the equity-based evaluation for spot mode, balance-based evaluation
for margin mode, the config split (`min_balance` / `min_equity`), and the live display state
tracking.

**AutoTrader pipeline only.** The Safety Circuit Breaker does not exist in backtesting — simulation
intentionally shows full consequences of an algorithm's behavior, including worst-case drawdowns.
See
[safety_circuit_breaker_architecture.md](../../architecture/safety_circuit_breaker_architecture.md)
for design rationale.

**Location:** `tests/autotrader/safety/`

---

## Test Structure

Two-level coverage:

```
tests/autotrader/safety/
├── test_safety_unit.py                  ← Level 1: _check_safety isolated (stub, no tick loop)
├── test_risk_baseline.py                ← Level 1: the denominator, and its survival of a restart
├── test_emergency_flatten.py            ← Level 1: the HARD stop — close, drain, then end
├── test_safety_report.py                ← Level 1: what the session RECORDED, capture + derive
├── test_safety_integration.py           ← Level 2: Spot mock sessions (kraken_spot/BTCUSD)
└── test_margin_safety_integration.py    ← Level 2: Margin mock sessions (mt5/EURUSD)
```

The quantity all of them measure against lives one suite away:
`docs/tests/framework/account_value_tests.md`.

### Level 1 — Unit Tests

Direct tests against `_check_safety` using a lightweight stub that mirrors the instance attributes the method reads/writes. No executor, no tick source, no bar rendering — pure logic validation.

| Class | What it validates |
|-------|-------------------|
| `TestSpotMinEquity` | `min_equity` triggers/clears in spot mode, `min_balance` inert in spot mode |
| `TestMarginMinBalance` | `min_balance` triggers/clears in margin mode, `min_equity` inert in margin mode |
| `TestDrawdown` | Equity-based drawdown (spot), balance-based drawdown (margin), no phantom drawdown after BUY (#270 core fix), threshold crossing, recovery |
| `TestCombinedConditions` | OR-combined: both fire, only min fires, only drawdown fires |
| `TestDisabled` | Zero thresholds disabled, `enabled=False` skips all checks |
| `TestDisplayState` | `_safety_current_value` stored, `_safety_drawdown_pct` stored, drawdown floored at 0% (profit case) |

### Level 1 — The Baseline (`test_risk_baseline.py`, #356)

The defect in two lines: a baseline captured on the first tick and held in memory means a restart
mid-drawdown re-anchors it at the already drawn-down value, and the accumulated loss is silently
forgotten. A thirty-day unattended run will restart.

| Class | What it validates |
|-------|-------------------|
| `TestTheRestartDrift` | a restored baseline is not re-anchored by the first tick, keeps its ORIGINAL stamp, and says `origin=restored_carry_over`; a first run still takes its own |
| `TestTakingItIsIdempotent` | `ensure_taken` runs every tick and only the first may act; no baseline reads as 0.0, which the breaker already treats as "no drawdown check" |
| `TestTheHighWaterMark` | rises with a peak, never falls with a drawdown, ignored entirely by a fixed baseline, and every advance carries its own date |
| `TestASpotRecordCanBeReDerived` | `value == quote + base × mark_price`; a margin record carries no price, because a cash-denominated account has none |
| `TestTheOperatorIsTold` | a restore is said out loud; a mode changed between runs is a warning and the RECORD wins |
| `TestTheExclusivityDeclarationTravelsWithIt` | the #489 declaration is stamped on the record — a drawdown percentage without it is a number about somebody else's deposits too |
| `TestARestoredKindGovernsTheBehaviourToo` | the restored kind becomes the session's MODE, both directions. Keeping the label while running the configured mode would convert the record on its first advance — the silent conversion one step later |

### Level 1 — The Hard Stop (`test_emergency_flatten.py`, #356)

Three severities exist and this is the third: a soft block stops new entries, HALT (#349) freezes
and waits for a human, this CLOSES. Driven against the real `_check_emergency_flatten` /
`_drive_emergency_flatten` bound to a stub.

| Class | What it validates |
|-------|-------------------|
| `TestItFiresOnlyWhenItShould` | a hard breach closes everything; a loss inside the threshold closes nothing; the master switch and `enabled=False` both close nothing; the absolute threshold fires too; it sends ONCE, not on every tick — a second round would double-sell what the first is already closing |
| `TestTheSessionEndsOnlyAfterTheFillsArrive` | it does not end while positions are open (EMERGENCY means immediate exit, and the fills arrive later); it ends once the book is flat; it ends anyway at the drain ceiling and NAMES what is still open; it ends once, not on every later tick |
| `TestSpotIsGatedAndTheAsymmetryIsDeliberate` | a spot holding is not sold by default and the operator hears why; the session still ends; with the switch on it is sold |
| `TestACloseThatCannotBeSent` | every position is attempted and each failure is named — a close that failed silently is still open at the venue |

### Level 1 — The Session Record (`test_safety_report.py`, #356 Phase C / #314)

Split the way the pipeline is (#391): the CAPTURE half runs against the real tick-loop methods
bound to a stub, the DERIVE half against the real builder. Nothing here renders.

| Class | What it validates |
|-------|-------------------|
| `TestTheExcursionIsARunningMaximum` | a recovered drawdown is still reported; a gain never becomes a negative drawdown; it is measured even with the limits switched OFF; the stamp comes from the canonical clock |
| `TestTheTwoExtremesCanBeTwoMoments` | under a high-water mark the deepest amount and the deepest share are different instants and both are kept; under the default fixed baseline they coincide |
| `TestTheBlockCountsEngagements` | a block held over many ticks counts once; cleared and returned counts twice; a session that ended clear still reports that it happened |
| `TestOneRowPerDay` | a day boundary files the day that ended; the new day starts from zero; the final incomplete day is filed by the capture; capturing twice does not file it twice; a day that tripped its own limit is marked |
| `TestAnUnresolvedHardStopIsNotReportedAsNeverFired` | a drain still running at session end resolves to "not confirmed" with the open positions named; a hard stop that never fired stays `None` |
| `TestTheReportNamesItsDenominator` | the baseline record travels whole; a carried baseline is flagged; a freshly struck one is not |
| `TestHowCloseItCame` | the share of the configured limit; `None` rather than 0.0 when no limit is configured; the NEARER of the percentage and absolute limits wins; the hard threshold is measured separately |
| `TestTheFloorIsOneQuantityWithTwoSpellings` | the report names the config key the account model writes, so one limit is not read as two |
| `TestTheWorstDayIsChosenInTheBuilder` | it names the DAY, not only the number; limit-hit days are counted; a session shorter than a day has no worst day |

### Level 2 — Integration Tests: Spot

End-to-end tests that run full AutoTrader mock sessions in **spot mode** with overridden safety
configs. Uses `btcusd_mock_safety.json` (kraken_spot/BTCUSD, 15K ticks, display off, INSTANT_FILL
mock adapter, `simple_consensus` decision logic). Three session scenarios, each run once and shared
across all tests in the module.

| Class | Safety Config | What it validates |
|-------|--------------|-------------------|
| `TestSpotSafetyNoFalsePositive` | `min_equity=100, max_drawdown_pct=50` | No false trigger, trades execute normally, no safety warnings |
| `TestSpotSafetyTriggers` | `min_equity=9999, max_drawdown_pct=0.01` | Circuit breaker triggers (spread cost alone exceeds threshold), session completes (soft stop) |
| `TestSafetyDisabledNoInterference` | `enabled=False` | Trades execute, no safety warnings, no interference |

### Level 2 — Integration Tests: Margin

End-to-end tests that run full AutoTrader mock sessions in **margin mode** with overridden safety
configs. Uses `margin_safety_test.json` (mt5/EURUSD, 15K ticks, display off, INSTANT_FILL mock
adapter, `backtesting_margin_stress` with deterministic `trade_sequence`). Three session scenarios.

| Class | Safety Config | What it validates |
|-------|--------------|-------------------|
| `TestMarginSafetyNoFalsePositive` | `min_balance=100, max_drawdown_pct=50` | No false trigger, trades execute normally, no safety warnings |
| `TestMarginSafetyTriggers` | `min_balance=10001, max_drawdown_pct=50` | Circuit breaker triggers (`min_balance` above initial), warning contains "min_balance" (not "min_equity"), no trades (all blocked) |
| `TestMarginSafetyDisabledNoInterference` | `enabled=False` | Trades execute, no safety warnings, no interference |

**Key difference from spot:** In margin mode, balance only changes when trades are **closed** (not
on open). The trigger session uses `min_balance=10001` (above the 10000 initial balance) to trigger
immediately, validating that the margin path correctly checks `min_balance`.

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

## Fixtures (conftest.py)

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
