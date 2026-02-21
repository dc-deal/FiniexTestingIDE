# SL/TP & Limit Order Validation Tests Documentation

## Overview

The SL/TP & limit order validation test suite verifies stop loss/take profit trigger detection, limit order fills, maker fees, close reason propagation, and order modifications. Each scenario uses real USDJPY extreme move time windows from the Discovery system to guarantee triggers/fills within the data range.

**Test Configuration:** `backtesting/sltp_limit_validation_test.json`
- Symbol: USDJPY (mt5)
- Account Currency: JPY (auto-detected)
- 9 scenarios: 5 SL/TP + 4 limit order, each opening 1 trade at tick 10
- Seeds: api_latency=12345, market_execution=67890
- Time windows sourced from `discoveries_cli.py extreme-moves mt5 USDJPY`

**Total Tests:** 32+ (SL/TP) + ~22 (limit order) = ~54

**Location:** `tests/sltp_limit_validation/`

---

## Test Structure

```
tests/
├── shared/
│   ├── fixture_helpers.py                ← extract_execution_stats() added here
│   └── shared_sltp_limit_validation.py   ← Reusable test classes (9 classes, ~54 tests)
├── sltp_limit_validation/
│   ├── conftest.py                       ← SLTP_LIMIT_VALIDATION_CONFIG = "backtesting/sltp_limit_validation_test.json"
│   └── test_sltp_limit_validation.py     ← Imports shared test classes
```

---

## Fixtures (conftest.py)

| Fixture | Scope | Description |
|---------|-------|-------------|
| `batch_execution_summary` | session | Runs all 5 scenarios once per session |
| `long_tp_tick_loop` | session | Tick loop for LONG TP scenario |
| `long_tp_trade_history` | session | TradeRecord list for LONG TP |
| `long_tp_execution_stats` | session | ExecutionStats for LONG TP |
| `long_sl_tick_loop` | session | Tick loop for LONG SL scenario |
| `long_sl_trade_history` | session | TradeRecord list for LONG SL |
| `long_sl_execution_stats` | session | ExecutionStats for LONG SL |
| `short_tp_tick_loop` | session | Tick loop for SHORT TP scenario |
| `short_tp_trade_history` | session | TradeRecord list for SHORT TP |
| `short_tp_execution_stats` | session | ExecutionStats for SHORT TP |
| `short_sl_tick_loop` | session | Tick loop for SHORT SL scenario |
| `short_sl_trade_history` | session | TradeRecord list for SHORT SL |
| `short_sl_execution_stats` | session | ExecutionStats for SHORT SL |
| `modify_tp_tick_loop` | session | Tick loop for modify TP scenario |
| `modify_tp_trade_history` | session | TradeRecord list for modify TP |
| `modify_tp_execution_stats` | session | ExecutionStats for modify TP |

---

## Test Classes

### TestLongTpTrigger (7 tests)
LONG position in an uptrend window (LONG extreme move #9, +128.1 pips). TP should trigger.

| Test | Validates |
|------|-----------|
| `test_trade_count` | Exactly 1 trade closed |
| `test_close_reason_is_tp` | close_reason = TP_TRIGGERED |
| `test_direction_is_long` | direction = LONG |
| `test_exit_price_equals_tp` | exit_price == take_profit (deterministic fill) |
| `test_tp_level_matches_config` | take_profit == 157.300 |
| `test_sl_level_matches_config` | stop_loss == 156.000 |
| `test_sl_tp_triggered_count` | ExecutionStats.sl_tp_triggered == 1 |

### TestLongSlTrigger (7 tests)
LONG position opened against a downtrend (SHORT extreme move #8, -175.0 pips). SL should trigger.

| Test | Validates |
|------|-----------|
| `test_trade_count` | Exactly 1 trade closed |
| `test_close_reason_is_sl` | close_reason = SL_TRIGGERED |
| `test_direction_is_long` | direction = LONG |
| `test_exit_price_equals_sl` | exit_price == stop_loss (deterministic fill) |
| `test_sl_level_matches_config` | stop_loss == 156.000 |
| `test_sl_tp_triggered_count` | ExecutionStats.sl_tp_triggered == 1 |
| `test_negative_pnl` | gross_pnl < 0 (loss confirmed) |

### TestShortTpTrigger (6 tests)
SHORT position in a downtrend window (SHORT extreme move #8, -175.0 pips). TP should trigger.

| Test | Validates |
|------|-----------|
| `test_trade_count` | Exactly 1 trade closed |
| `test_close_reason_is_tp` | close_reason = TP_TRIGGERED |
| `test_direction_is_short` | direction = SHORT |
| `test_exit_price_equals_tp` | exit_price == take_profit (deterministic fill) |
| `test_tp_level_matches_config` | take_profit == 156.000 |
| `test_sl_tp_triggered_count` | ExecutionStats.sl_tp_triggered == 1 |

### TestShortSlTrigger (7 tests)
SHORT position opened against an uptrend (LONG extreme move #7, +102.1 pips). SL should trigger.

| Test | Validates |
|------|-----------|
| `test_trade_count` | Exactly 1 trade closed |
| `test_close_reason_is_sl` | close_reason = SL_TRIGGERED |
| `test_direction_is_short` | direction = SHORT |
| `test_exit_price_equals_sl` | exit_price == stop_loss (deterministic fill) |
| `test_sl_level_matches_config` | stop_loss == 156.300 |
| `test_sl_tp_triggered_count` | ExecutionStats.sl_tp_triggered == 1 |
| `test_negative_pnl` | gross_pnl < 0 (loss confirmed) |

### TestModifyTpTrigger (5 tests)
LONG position with in-flight TP modification. Initial TP=160.000 (unreachable), modified at tick 500 to 157.300 (triggers).

| Test | Validates |
|------|-----------|
| `test_trade_count` | Exactly 1 trade closed |
| `test_close_reason_is_tp` | close_reason = TP_TRIGGERED |
| `test_tp_is_modified_value` | take_profit == 157.300 (not original 160.000) |
| `test_exit_price_equals_modified_tp` | exit_price == 157.300 |
| `test_sl_tp_triggered_count` | ExecutionStats.sl_tp_triggered == 1 |

---

## Scenario Design

All scenarios use `hold_ticks=999999` to ensure the position stays open until SL/TP triggers — the hold timer never expires before the price level is reached.

### Time Window Selection

Scenarios use real extreme move windows from the Discovery system (`discoveries_cli.py extreme-moves mt5 USDJPY`). This guarantees sufficient price movement to trigger SL/TP within the data range.

| Scenario | Discovery Source | Window | Direction vs Trend |
|----------|-----------------|--------|--------------------|
| `long_tp_trigger` | LONG #9 (+128.1 pips) | 2026-01-08 → 2026-01-09 | With trend |
| `long_sl_trigger` | SHORT #8 (-175.0 pips) | 2025-12-10 → 2025-12-12 | Against trend |
| `short_tp_trigger` | SHORT #8 (-175.0 pips) | 2025-12-10 → 2025-12-12 | With trend |
| `short_sl_trigger` | LONG #7 (+102.1 pips) | 2025-12-30 → 2026-01-01 | Against trend |
| `modify_tp_trigger` | LONG #9 (+128.1 pips) | 2026-01-08 → 2026-01-09 | With trend |

### SL/TP Trigger Mechanics

- **LONG SL**: triggers when `bid <= stop_loss`
- **LONG TP**: triggers when `bid >= take_profit`
- **SHORT SL**: triggers when `ask >= stop_loss`
- **SHORT TP**: triggers when `ask <= take_profit`
- **Fill price**: SL/TP level itself (deterministic, bypasses latency pipeline)
- **CloseReason**: `SL_TRIGGERED` or `TP_TRIGGERED` propagated to TradeRecord

### Modify Sequence

The `modify_tp_trigger` scenario demonstrates `modify_sequence` — a BacktestingDeterministic config concept that calls `modify_position()` at a specific tick:

```json
"modify_sequence": [
    { "tick_number": 500, "take_profit": 157.300 }
]
```

The original TP (160.000) is unreachable in the data range. After modification at tick 500, the new TP (157.300) triggers normally.

---

## Key Design Decisions

### Discovery-Driven Test Data
Instead of synthetic price data, this suite uses real market data windows identified by the Extreme Move Scanner. This validates SL/TP behavior under realistic market conditions with real spreads and tick patterns.

### _started_trade_indices Guard
After SL/TP closes a position, BacktestingDeterministic must not re-open it. The `_started_trade_indices` set tracks which trades from `trade_sequence` have been submitted. Combined with an API state check (no pending orders, no open positions), this prevents the trade loop from triggering again.

### Deterministic Fill at SL/TP Level
SL/TP closes bypass the latency pipeline entirely. The fill price equals the configured SL/TP level, making assertions deterministic and independent of market microstructure.

---

## Running the Tests

```bash
# Run only SL/TP validation tests
pytest tests/sltp_limit_validation/ -v

# Run with output (shows scenario execution)
pytest tests/sltp_limit_validation/ -v -s
```
