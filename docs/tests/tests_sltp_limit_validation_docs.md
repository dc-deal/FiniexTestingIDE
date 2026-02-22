# SL/TP & Limit Order Validation Tests Documentation

## Overview

The SL/TP & limit order validation test suite verifies stop loss/take profit trigger detection, limit order fills, maker fees, close reason propagation, and order modifications. Each scenario uses real USDJPY extreme move time windows from the Discovery system to guarantee triggers/fills within the data range.

**Test Configuration:** `backtesting/sltp_limit_validation_test.json`
- Symbol: USDJPY (mt5)
- Account Currency: JPY (auto-detected)
- 17 scenarios: 5 SL/TP + 4 limit order + 7 stop order + 1 cancel limit, each opening 1 trade at tick 10
- Seeds: api_latency=12345, market_execution=67890
- Time windows sourced from `discoveries_cli.py extreme-moves mt5 USDJPY`

**Total Tests:** ~54 (SL/TP + limit) + ~27 (stop orders) + 1 (cancel limit) = ~82

**Location:** `tests/sltp_limit_validation/`

---

## Test Structure

```
tests/
├── shared/
│   ├── fixture_helpers.py                ← extract_execution_stats() added here
│   └── shared_sltp_limit_validation.py   ← Reusable test classes (17 classes, ~82 tests)
├── sltp_limit_validation/
│   ├── conftest.py                       ← SLTP_LIMIT_VALIDATION_CONFIG = "backtesting/sltp_limit_validation_test.json"
│   └── test_sltp_limit_validation.py     ← Imports shared test classes
```

---

## Fixtures (conftest.py)

| Fixture | Scope | Description |
|---------|-------|-------------|
| `batch_execution_summary` | session | Runs all 17 scenarios once per session |
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
| `long_limit_fill_tick_loop` | session | Tick loop for LONG limit fill scenario |
| `long_limit_fill_trade_history` | session | TradeRecord list for LONG limit fill |
| `long_limit_fill_execution_stats` | session | ExecutionStats for LONG limit fill |
| `short_limit_fill_tick_loop` | session | Tick loop for SHORT limit fill scenario |
| `short_limit_fill_trade_history` | session | TradeRecord list for SHORT limit fill |
| `short_limit_fill_execution_stats` | session | ExecutionStats for SHORT limit fill |
| `limit_sl_tick_loop` | session | Tick loop for limit fill then SL scenario |
| `limit_sl_trade_history` | session | TradeRecord list for limit + SL |
| `limit_sl_execution_stats` | session | ExecutionStats for limit + SL |
| `modify_limit_tick_loop` | session | Tick loop for modify limit price scenario |
| `modify_limit_trade_history` | session | TradeRecord list for modify limit |
| `modify_limit_execution_stats` | session | ExecutionStats for modify limit |
| `stop_long_tick_loop` | session | Tick loop for STOP LONG trigger scenario |
| `stop_long_trade_history` | session | TradeRecord list for STOP LONG |
| `stop_long_execution_stats` | session | ExecutionStats for STOP LONG |
| `stop_short_tick_loop` | session | Tick loop for STOP SHORT trigger scenario |
| `stop_short_trade_history` | session | TradeRecord list for STOP SHORT |
| `stop_short_execution_stats` | session | ExecutionStats for STOP SHORT |
| `stop_limit_long_tick_loop` | session | Tick loop for STOP_LIMIT LONG scenario |
| `stop_limit_long_trade_history` | session | TradeRecord list for STOP_LIMIT LONG |
| `stop_limit_long_execution_stats` | session | ExecutionStats for STOP_LIMIT LONG |
| `stop_limit_short_tick_loop` | session | Tick loop for STOP_LIMIT SHORT scenario |
| `stop_limit_short_trade_history` | session | TradeRecord list for STOP_LIMIT SHORT |
| `stop_limit_short_execution_stats` | session | ExecutionStats for STOP_LIMIT SHORT |
| `stop_tp_tick_loop` | session | Tick loop for STOP LONG then TP scenario |
| `stop_tp_trade_history` | session | TradeRecord list for STOP + TP |
| `stop_tp_execution_stats` | session | ExecutionStats for STOP + TP |
| `modify_stop_tick_loop` | session | Tick loop for modify stop trigger scenario |
| `modify_stop_trade_history` | session | TradeRecord list for modify stop |
| `modify_stop_execution_stats` | session | ExecutionStats for modify stop |
| `cancel_stop_tick_loop` | session | Tick loop for cancel stop scenario |
| `cancel_stop_trade_history` | session | TradeRecord list for cancel stop |
| `cancel_stop_execution_stats` | session | ExecutionStats for cancel stop |
| `cancel_limit_tick_loop` | session | Tick loop for cancel limit scenario |
| `cancel_limit_trade_history` | session | TradeRecord list for cancel limit |
| `cancel_limit_execution_stats` | session | ExecutionStats for cancel limit |

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

### TestStopLongTrigger (5 tests)
STOP LONG order — stop triggers when uptrend pushes price above stop_price. Fills at market price (taker fee).

| Test | Validates |
|------|-----------|
| `test_trade_count` | Exactly 1 trade |
| `test_entry_type_is_stop` | entry_type = STOP |
| `test_entry_price_at_or_above_stop` | entry_price >= 157.000 (market fill after trigger) |
| `test_direction_is_long` | direction = LONG |
| `test_close_reason_scenario_end` | close_reason = SCENARIO_END |

### TestStopShortTrigger (5 tests)
STOP SHORT order — stop triggers when downtrend pushes price below stop_price. Fills at market price (taker fee).

| Test | Validates |
|------|-----------|
| `test_trade_count` | Exactly 1 trade |
| `test_entry_type_is_stop` | entry_type = STOP |
| `test_entry_price_at_or_below_stop` | entry_price <= 156.200 (market fill after trigger) |
| `test_direction_is_short` | direction = SHORT |
| `test_close_reason_scenario_end` | close_reason = SCENARIO_END |

### TestStopLimitLongTrigger (5 tests)
STOP_LIMIT LONG — stop at 157.000 triggers, then fills as LIMIT at 157.200 (maker fee).

| Test | Validates |
|------|-----------|
| `test_trade_count` | Exactly 1 trade |
| `test_entry_type_is_stop_limit` | entry_type = STOP_LIMIT |
| `test_entry_price_equals_limit` | entry_price == 157.200 (limit fill, not market) |
| `test_direction_is_long` | direction = LONG |
| `test_close_reason_scenario_end` | close_reason = SCENARIO_END |

### TestStopLimitShortTrigger (5 tests)
STOP_LIMIT SHORT — stop at 156.200 triggers, then fills as LIMIT at 156.000 (maker fee).

| Test | Validates |
|------|-----------|
| `test_trade_count` | Exactly 1 trade |
| `test_entry_type_is_stop_limit` | entry_type = STOP_LIMIT |
| `test_entry_price_equals_limit` | entry_price == 156.000 (limit fill, not market) |
| `test_direction_is_short` | direction = SHORT |
| `test_close_reason_scenario_end` | close_reason = SCENARIO_END |

### TestStopLongThenTp (5 tests)
STOP LONG triggers at stop_price=156.800, position opened; then TP=157.300 closes it.

| Test | Validates |
|------|-----------|
| `test_trade_count` | Exactly 1 trade |
| `test_entry_type_is_stop` | entry_type = STOP |
| `test_close_reason_is_tp` | close_reason = TP_TRIGGERED |
| `test_exit_price_equals_tp` | exit_price == 157.300 |
| `test_sl_tp_triggered_count` | ExecutionStats.sl_tp_triggered == 1 |

### TestModifyStopTrigger (4 tests)
Stop order with initial stop_price=158.000 (unreachable). Modified at tick 500 to 157.000 (triggers).

| Test | Validates |
|------|-----------|
| `test_trade_count` | Exactly 1 trade (triggers after modification) |
| `test_entry_type_is_stop` | entry_type = STOP |
| `test_entry_price_at_or_above_modified_stop` | entry_price >= 157.000 |
| `test_direction_is_long` | direction = LONG |

### TestCancelStopNoFill (1 test)
STOP LONG cancelled at tick 100 before it can trigger. No position opened.

| Test | Validates |
|------|-----------|
| `test_no_trades` | 0 trades (cancel prevented fill) |

### TestCancelLimitNoFill (1 test)
LONG LIMIT at utopian price (150.000) cancelled at tick 100 before fill. No position opened.

| Test | Validates |
|------|-----------|
| `test_no_trades` | 0 trades (cancel prevented fill) |

---

## Scenario Design

All scenarios use `hold_ticks=999999` to ensure the position stays open until SL/TP triggers — the hold timer never expires before the price level is reached.

### Time Window Selection

Scenarios use real extreme move windows from the Discovery system (`discoveries_cli.py extreme-moves mt5 USDJPY`). This guarantees sufficient price movement to trigger SL/TP within the data range.

| Scenario | Discovery Source | Window | Notes |
|----------|-----------------|--------|-------|
| `long_tp_trigger` | LONG #9 (+128.1 pips) | 2026-01-08 → 2026-01-09 | With trend |
| `long_sl_trigger` | SHORT #8 (-175.0 pips) | 2025-12-10 → 2025-12-12 | Against trend |
| `short_tp_trigger` | SHORT #8 (-175.0 pips) | 2025-12-10 → 2025-12-12 | With trend |
| `short_sl_trigger` | LONG #7 (+102.1 pips) | 2025-12-30 → 2026-01-01 | Against trend |
| `modify_tp_trigger` | LONG #9 (+128.1 pips) | 2026-01-08 → 2026-01-09 | With trend, TP modified at tick 500 |
| `long_limit_fill` | SHORT #8 (-175.0 pips) | 2025-12-10 → 2025-12-12 | LONG limit at 156.000, price dips to fill |
| `short_limit_fill` | LONG #9 (+128.1 pips) | 2026-01-08 → 2026-01-09 | SHORT limit at 157.300, price rises to fill |
| `limit_fill_then_sl` | SHORT #8 (-175.0 pips) | 2025-12-10 → 2025-12-12 | LONG limit at 156.500, SL=155.800 |
| `modify_limit_price_fill` | SHORT #8 (-175.0 pips) | 2025-12-10 → 2025-12-12 | LONG limit modified from 155.000→156.200 |
| `stop_long_trigger` | LONG #9 (+128.1 pips) | 2026-01-08 → 2026-01-09 | STOP at 157.000, market fill |
| `stop_short_trigger` | SHORT #8 (-175.0 pips) | 2025-12-10 → 2025-12-12 | STOP at 156.200, market fill |
| `stop_limit_long_trigger` | LONG #9 (+128.1 pips) | 2026-01-08 → 2026-01-09 | STOP at 157.000, limit fill at 157.200 |
| `stop_limit_short_trigger` | SHORT #8 (-175.0 pips) | 2025-12-10 → 2025-12-12 | STOP at 156.200, limit fill at 156.000 |
| `stop_long_then_tp` | LONG #9 (+128.1 pips) | 2026-01-08 → 2026-01-09 | STOP at 156.800, TP=157.300 |
| `modify_stop_trigger` | LONG #9 (+128.1 pips) | 2026-01-08 → 2026-01-09 | STOP 158.000→157.000 at tick 500 |
| `cancel_stop_no_fill` | LONG #9 (+128.1 pips) | 2026-01-08 → 2026-01-09 | Cancelled at tick 100, 0 trades |
| `cancel_limit_no_fill` | LONG #9 (+128.1 pips) | 2026-01-08 → 2026-01-09 | LIMIT at 150.000, cancelled at tick 100, 0 trades |

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

### Stop Order Trigger Mechanics

- **STOP LONG**: triggers when `ask >= stop_price` → fills at market (taker fee), `entry_type=STOP`
- **STOP SHORT**: triggers when `bid <= stop_price` → fills at market (taker fee), `entry_type=STOP`
- **STOP_LIMIT LONG**: triggers when `ask >= stop_price` → converts to LIMIT → fills at `limit_price` (maker fee), `entry_type=STOP_LIMIT`
- **STOP_LIMIT SHORT**: triggers when `bid <= stop_price` → converts to LIMIT → fills at `limit_price` (maker fee), `entry_type=STOP_LIMIT`

### Modify Stop Sequence

`modify_stop_sequence` calls `modify_stop_order()` via DecisionTradingAPI at a configured tick:

```json
"modify_stop_sequence": [
    { "tick_number": 500, "stop_price": 157.000 }
]
```

The `modify_stop_trigger` scenario places an unreachable stop (158.000) then modifies it at tick 500 to 157.000, which triggers within the data window.

### Cancel Stop Sequence

`cancel_stop_sequence` calls `cancel_stop_order()` at a configured tick, clearing the tracked order ID:

```json
"cancel_stop_sequence": [
    { "tick_number": 100 }
]
```

The `cancel_stop_no_fill` scenario verifies that cancellation prevents any fill — 0 trades expected.

### Cancel Limit Sequence

`cancel_limit_sequence` calls `cancel_limit_order()` at a configured tick, clearing the tracked order ID:

```json
"cancel_limit_sequence": [
    { "tick_number": 100 }
]
```

The `cancel_limit_no_fill` scenario places a LONG LIMIT at an unreachable price (150.000) then cancels it at tick 100 — 0 trades expected.

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
