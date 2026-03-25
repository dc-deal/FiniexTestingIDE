# SL/TP & Limit Order Validation Tests Documentation

## Overview

The SL/TP & limit order validation test suite verifies stop loss/take profit trigger detection, limit order fills, maker fees, close reason propagation, and order modifications. Each scenario uses real USDJPY extreme move time windows from the Discovery system to guarantee triggers/fills within the data range.

**Test Configuration:** `backtesting/sltp_limit_validation_test.json`
- Symbol: USDJPY (mt5)
- Account Currency: JPY (auto-detected)
- 17 scenarios: 5 SL/TP + 4 limit order + 7 stop order + 1 cancel limit, each opening 1 trade at tick 10
- Seeds: inbound_latency=12345
- Time windows sourced from `discoveries_cli.py extreme-moves mt5 USDJPY`
- Per-scenario `max_ticks` caps to limit tick loop processing (see Scenario Design)

**Total Tests:** 85 (SL/TP + limit + stop + cancel + batch health)

**Performance:** ~18s (50-bar discovery windows + tightened max_ticks caps)

**Location:** `tests/sltp_limit_validation/`

---

## Config-Driven Test Pattern

Tests in this suite use **no hardcoded price values**. Instead, expected trade levels (SL, TP, limit price, stop price) are extracted at runtime from the scenario config JSON via `ScenarioExpectedValues`.

### How It Works

```
Extreme Moves Report ──> Config JSON ──> ScenarioExpectedValues ──> Test Assertions
     (discovery)         (price levels)    (runtime extraction)      (fully independent)
```

1. **Extreme Moves Report** (`discoveries_cli.py extreme-moves mt5 USDJPY`) identifies time windows with guaranteed directional price movement (50-bar windows with sufficient pip range)
2. **Config JSON** (`sltp_limit_validation_test.json`) encodes the discovered entry prices, SL/TP levels, limit/stop prices per scenario
3. **`ScenarioExpectedValues`** (`tests/shared/fixture_helpers.py`) extracts effective expected values from the config at test time, including modify sequence overrides
4. **Test assertions** compare actual trade results against config-extracted values — zero hardcoded prices in the test file

This means switching to different discovery windows only requires updating the config JSON. Tests adapt automatically.

### ScenarioExpectedValues Extraction

The `extract_scenario_expected_values()` function reads `trade_sequence[0]` for base values, then applies overrides:
- `modify_sequence` overrides `stop_loss` and/or `take_profit`
- `modify_limit_sequence` overrides `price`
- `modify_stop_sequence` overrides `stop_price`

---

## Test Structure

```
tests/
├── shared/
│   ├── fixture_helpers.py                ← ScenarioExpectedValues, extract functions
│   ├── shared_batch_health.py            ← TestBatchHealth (all-suite guard)
│   └── shared_sltp_limit_validation.py   ← Reusable test classes (17 classes, ~82 tests)
├── sltp_limit_validation/
│   ├── conftest.py                       ← Session fixtures + *_expected value fixtures
│   └── test_sltp_limit_validation.py     ← Imports shared test classes + TestBatchHealth
```

---

## Fixtures (conftest.py)

### Expected Value Fixtures

Each scenario that asserts price levels has a `*_expected` fixture providing `ScenarioExpectedValues` extracted from the config.

| Fixture | Scenario Index | Provides |
|---------|---------------|----------|
| `scenario_config` | — | Raw config JSON (session scope) |
| `long_tp_expected` | 0 | SL/TP levels for LONG TP |
| `long_sl_expected` | 1 | SL/TP levels for LONG SL |
| `short_tp_expected` | 2 | SL/TP levels for SHORT TP |
| `short_sl_expected` | 3 | SL/TP levels for SHORT SL |
| `modify_tp_expected` | 4 | Modified TP level |
| `long_limit_fill_expected` | 5 | Limit price for LONG fill |
| `short_limit_fill_expected` | 6 | Limit price for SHORT fill |
| `limit_sl_expected` | 7 | Limit price + SL level |
| `modify_limit_expected` | 8 | Modified limit price |
| `stop_long_expected` | 9 | Stop price for LONG |
| `stop_short_expected` | 10 | Stop price for SHORT |
| `stop_limit_long_expected` | 11 | Stop + limit price for LONG |
| `stop_limit_short_expected` | 12 | Stop + limit price for SHORT |
| `stop_tp_expected` | 13 | Stop price + TP level |
| `modify_stop_expected` | 14 | Modified stop price |

### Per-Scenario Data Fixtures

Each scenario has three fixtures (all session scope):

| Pattern | Description |
|---------|-------------|
| `*_tick_loop` | `ProcessTickLoopResult` for the scenario |
| `*_trade_history` | `List[TradeRecord]` for the scenario |
| `*_execution_stats` | `ExecutionStats` for the scenario |

Scenarios: `long_tp`, `long_sl`, `short_tp`, `short_sl`, `modify_tp`, `long_limit_fill`, `short_limit_fill`, `limit_sl`, `modify_limit`, `stop_long`, `stop_short`, `stop_limit_long`, `stop_limit_short`, `stop_tp`, `modify_stop`, `cancel_stop`, `cancel_limit`

---

## Test Classes

### TestLongTpTrigger (7 tests)
LONG position in uptrend window. TP should trigger.

| Test | Validates |
|------|-----------|
| `test_trade_count` | Exactly 1 trade closed |
| `test_close_reason_is_tp` | close_reason = TP_TRIGGERED |
| `test_direction_is_long` | direction = LONG |
| `test_exit_price_equals_tp` | exit_price == take_profit (deterministic fill) |
| `test_tp_level_matches_config` | take_profit == config value |
| `test_sl_level_matches_config` | stop_loss == config value |
| `test_sl_tp_triggered_count` | ExecutionStats.sl_tp_triggered == 1 |

### TestLongSlTrigger (7 tests)
LONG position opened against downtrend. SL should trigger.

| Test | Validates |
|------|-----------|
| `test_trade_count` | Exactly 1 trade closed |
| `test_close_reason_is_sl` | close_reason = SL_TRIGGERED |
| `test_direction_is_long` | direction = LONG |
| `test_exit_price_equals_sl` | exit_price == stop_loss (deterministic fill) |
| `test_sl_level_matches_config` | stop_loss == config value |
| `test_sl_tp_triggered_count` | ExecutionStats.sl_tp_triggered == 1 |
| `test_negative_pnl` | gross_pnl < 0 (loss confirmed) |

### TestShortTpTrigger (6 tests)
SHORT position in downtrend window. TP should trigger.

| Test | Validates |
|------|-----------|
| `test_trade_count` | Exactly 1 trade closed |
| `test_close_reason_is_tp` | close_reason = TP_TRIGGERED |
| `test_direction_is_short` | direction = SHORT |
| `test_exit_price_equals_tp` | exit_price == take_profit (deterministic fill) |
| `test_tp_level_matches_config` | take_profit == config value |
| `test_sl_tp_triggered_count` | ExecutionStats.sl_tp_triggered == 1 |

### TestShortSlTrigger (7 tests)
SHORT position opened against uptrend. SL should trigger.

| Test | Validates |
|------|-----------|
| `test_trade_count` | Exactly 1 trade closed |
| `test_close_reason_is_sl` | close_reason = SL_TRIGGERED |
| `test_direction_is_short` | direction = SHORT |
| `test_exit_price_equals_sl` | exit_price == stop_loss (deterministic fill) |
| `test_sl_level_matches_config` | stop_loss == config value |
| `test_sl_tp_triggered_count` | ExecutionStats.sl_tp_triggered == 1 |
| `test_negative_pnl` | gross_pnl < 0 (loss confirmed) |

### TestModifyTpTrigger (5 tests)
LONG position with in-flight TP modification. Initial TP unreachable, modified at tick 500 to reachable value.

| Test | Validates |
|------|-----------|
| `test_trade_count` | Exactly 1 trade closed |
| `test_close_reason_is_tp` | close_reason = TP_TRIGGERED |
| `test_tp_is_modified_value` | take_profit == modified config value |
| `test_exit_price_equals_modified_tp` | exit_price == modified TP |
| `test_sl_tp_triggered_count` | ExecutionStats.sl_tp_triggered == 1 |

### TestLongLimitFill (5 tests)
LONG limit order. Price dips to fill level (maker fee).

| Test | Validates |
|------|-----------|
| `test_trade_count` | Exactly 1 trade |
| `test_entry_type_is_limit` | entry_type = LIMIT |
| `test_entry_price_equals_limit` | entry_price == config limit price |
| `test_direction_is_long` | direction = LONG |
| `test_close_reason_scenario_end` | close_reason = SCENARIO_END |

### TestShortLimitFill (5 tests)
SHORT limit order. Price rises to fill level (maker fee).

| Test | Validates |
|------|-----------|
| `test_trade_count` | Exactly 1 trade |
| `test_entry_type_is_limit` | entry_type = LIMIT |
| `test_entry_price_equals_limit` | entry_price == config limit price |
| `test_direction_is_short` | direction = SHORT |
| `test_close_reason_scenario_end` | close_reason = SCENARIO_END |

### TestLimitFillThenSl (5 tests)
LONG limit fills, then SL triggers during continued downtrend.

| Test | Validates |
|------|-----------|
| `test_trade_count` | Exactly 1 trade |
| `test_close_reason_is_sl` | close_reason = SL_TRIGGERED |
| `test_exit_price_equals_sl` | exit_price == config SL level |
| `test_entry_price_equals_limit` | entry_price == config limit price |
| `test_sl_tp_triggered_count` | ExecutionStats.sl_tp_triggered == 1 |

### TestModifyLimitPriceFill (4 tests)
LONG limit with price modification. Original price unreachable, modified at tick 500 to reachable value.

| Test | Validates |
|------|-----------|
| `test_trade_count` | Exactly 1 trade |
| `test_entry_type_is_limit` | entry_type = LIMIT |
| `test_entry_price_equals_modified` | entry_price == modified config limit price |
| `test_direction_is_long` | direction = LONG |

### TestStopLongTrigger (5 tests)
STOP LONG order — stop triggers when uptrend pushes price above stop_price. Fills at market price (taker fee).

| Test | Validates |
|------|-----------|
| `test_trade_count` | Exactly 1 trade |
| `test_entry_type_is_stop` | entry_type = STOP |
| `test_entry_price_at_or_above_stop` | entry_price >= config stop_price |
| `test_direction_is_long` | direction = LONG |
| `test_close_reason_scenario_end` | close_reason = SCENARIO_END |

### TestStopShortTrigger (5 tests)
STOP SHORT order — stop triggers when downtrend pushes price below stop_price. Fills at market price (taker fee).

| Test | Validates |
|------|-----------|
| `test_trade_count` | Exactly 1 trade |
| `test_entry_type_is_stop` | entry_type = STOP |
| `test_entry_price_at_or_below_stop` | entry_price <= config stop_price |
| `test_direction_is_short` | direction = SHORT |
| `test_close_reason_scenario_end` | close_reason = SCENARIO_END |

### TestStopLimitLongTrigger (5 tests)
STOP_LIMIT LONG — stop triggers, then fills as LIMIT at configured limit price (maker fee).

| Test | Validates |
|------|-----------|
| `test_trade_count` | Exactly 1 trade |
| `test_entry_type_is_stop_limit` | entry_type = STOP_LIMIT |
| `test_entry_price_equals_limit` | entry_price == config limit price |
| `test_direction_is_long` | direction = LONG |
| `test_close_reason_scenario_end` | close_reason = SCENARIO_END |

### TestStopLimitShortTrigger (5 tests)
STOP_LIMIT SHORT — stop triggers, then fills as LIMIT at configured limit price (maker fee).

| Test | Validates |
|------|-----------|
| `test_trade_count` | Exactly 1 trade |
| `test_entry_type_is_stop_limit` | entry_type = STOP_LIMIT |
| `test_entry_price_equals_limit` | entry_price == config limit price |
| `test_direction_is_short` | direction = SHORT |
| `test_close_reason_scenario_end` | close_reason = SCENARIO_END |

### TestStopLongThenTp (5 tests)
STOP LONG triggers, position opened; then TP closes it.

| Test | Validates |
|------|-----------|
| `test_trade_count` | Exactly 1 trade |
| `test_entry_type_is_stop` | entry_type = STOP |
| `test_close_reason_is_tp` | close_reason = TP_TRIGGERED |
| `test_exit_price_equals_tp` | exit_price == config TP level |
| `test_sl_tp_triggered_count` | ExecutionStats.sl_tp_triggered == 1 |

### TestModifyStopTrigger (4 tests)
Stop order with unreachable initial stop_price. Modified at tick 500 to reachable value.

| Test | Validates |
|------|-----------|
| `test_trade_count` | Exactly 1 trade (triggers after modification) |
| `test_entry_type_is_stop` | entry_type = STOP |
| `test_entry_price_at_or_above_modified_stop` | entry_price >= modified config stop_price |
| `test_direction_is_long` | direction = LONG |

### TestCancelStopNoFill (1 test)
STOP LONG cancelled at tick 100 before it can trigger. No position opened.

| Test | Validates |
|------|-----------|
| `test_no_trades` | 0 trades (cancel prevented fill) |

### TestCancelLimitNoFill (1 test)
LONG LIMIT at utopian price cancelled at tick 100 before fill. No position opened.

| Test | Validates |
|------|-----------|
| `test_no_trades` | 0 trades (cancel prevented fill) |

---

## Scenario Design

All scenarios use `hold_ticks=999999` to ensure the position stays open until SL/TP triggers — the hold timer never expires before the price level is reached.

### Time Window Selection

Scenarios use real extreme move windows from the Discovery system (`discoveries_cli.py extreme-moves mt5 USDJPY`). Windows are selected with **50-bar resolution** — small enough for fast execution, large enough to guarantee sufficient pip movement for all trigger/fill scenarios.

| Window | Discovery Source | Start | End | Entry Price | Extreme | Ticks |
|--------|-----------------|-------|-----|-------------|---------|-------|
| Uptrend | LONG #28 (+49.7 pips) | 2026-01-05T18:55 | 2026-01-05T23:00 | ~156.209 | 156.706 | 7,786 |
| Downtrend | SHORT #28 (-37.8 pips) | 2025-10-21T20:55 | 2025-10-22T01:00 | ~151.924 | 151.546 | 10,845 |

Scenario-to-window mapping:

| Scenario | Window | Notes |
|----------|--------|-------|
| `long_tp_trigger` | Uptrend | With trend |
| `long_sl_trigger` | Downtrend | Against trend |
| `short_tp_trigger` | Downtrend | With trend |
| `short_sl_trigger` | Uptrend | Against trend |
| `modify_tp_trigger` | Uptrend | With trend, TP modified at tick 500 |
| `long_limit_fill` | Downtrend | LONG limit, price dips to fill |
| `short_limit_fill` | Uptrend | SHORT limit, price rises to fill |
| `limit_fill_then_sl` | Downtrend | LONG limit fills, SL triggers |
| `modify_limit_price_fill` | Downtrend | LONG limit modified at tick 500 |
| `stop_long_trigger` | Uptrend | STOP at stop_price, market fill |
| `stop_short_trigger` | Downtrend | STOP at stop_price, market fill |
| `stop_limit_long_trigger` | Uptrend | STOP triggers, limit fill |
| `stop_limit_short_trigger` | Downtrend | STOP triggers, limit fill |
| `stop_long_then_tp` | Uptrend | STOP fills, TP triggers |
| `modify_stop_trigger` | Uptrend | STOP modified at tick 500 |
| `cancel_stop_no_fill` | Uptrend | Cancelled at tick 100, 0 trades |
| `cancel_limit_no_fill` | Uptrend | Cancelled at tick 100, 0 trades |

### Tick Cap (`max_ticks`)

Each scenario has a `max_ticks` cap set to the trigger/fill tick + ~10% headroom. This avoids processing thousands of idle ticks after the relevant event has fired.

| Scenario | Trigger Tick | max_ticks | Rationale |
|----------|-------------|-----------|-----------|
| `long_tp_trigger` | ~5,434 (TP) | 6,000 | TP fires, rest is idle |
| `long_sl_trigger` | ~5,769 (SL) | 6,500 | SL fires, rest is idle |
| `short_tp_trigger` | ~6,898 (TP) | 7,500 | TP fires, rest is idle |
| `short_sl_trigger` | ~5,016 (SL) | 5,500 | SL fires, rest is idle |
| `modify_tp_trigger` | ~5,434 (TP) | 6,000 | Modify@500, TP fires |
| `long_limit_fill` | ~5,900 (fill) | 6,500 | Limit fills, SCENARIO_END |
| `short_limit_fill` | ~5,400 (fill) | 6,000 | Limit fills, SCENARIO_END |
| `limit_fill_then_sl` | ~7,977 (SL) | 8,500 | Limit fills, SL fires |
| `modify_limit_price_fill` | ~5,900 (fill) | 6,500 | Modify@500, limit fills |
| `stop_long_trigger` | ~3,518 (fill) | 4,000 | Stop triggers early |
| `stop_short_trigger` | ~5,900 (fill) | 6,500 | Stop triggers, SCENARIO_END |
| `stop_limit_long_trigger` | ~3,518 (fill) | 4,000 | Stop triggers, limit fills |
| `stop_limit_short_trigger` | ~5,900 (fill) | 6,500 | Stop triggers, limit fills |
| `stop_long_then_tp` | ~5,434 (TP) | 6,000 | Stop fills, TP fires |
| `modify_stop_trigger` | ~3,518 (fill) | 4,000 | Modify@500, stop triggers |
| `cancel_stop_no_fill` | 100 (cancel) | 1,500 | Cancel at tick 100, 0 trades |
| `cancel_limit_no_fill` | 100 (cancel) | 1,500 | Cancel at tick 100, 0 trades |

Total ticks processed: ~85k (vs ~18.6k ticks in the old multi-day windows). Suite completes in ~18s.

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
    { "tick_number": 500, "take_profit": 156.550 }
]
```

The original TP is unreachable in the data range. After modification at tick 500, the new TP triggers normally.

### Stop Order Trigger Mechanics

- **STOP LONG**: triggers when `ask >= stop_price` -> fills at market (taker fee), `entry_type=STOP`
- **STOP SHORT**: triggers when `bid <= stop_price` -> fills at market (taker fee), `entry_type=STOP`
- **STOP_LIMIT LONG**: triggers when `ask >= stop_price` -> converts to LIMIT -> fills at `limit_price` (maker fee), `entry_type=STOP_LIMIT`
- **STOP_LIMIT SHORT**: triggers when `bid <= stop_price` -> converts to LIMIT -> fills at `limit_price` (maker fee), `entry_type=STOP_LIMIT`

### Modify Stop Sequence

`modify_stop_sequence` calls `modify_stop_order()` via DecisionTradingApi at a configured tick:

```json
"modify_stop_sequence": [
    { "tick_number": 500, "stop_price": 156.400 }
]
```

The `modify_stop_trigger` scenario places an unreachable stop then modifies it at tick 500 to a reachable value, which triggers within the data window.

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

The `cancel_limit_no_fill` scenario places a LONG LIMIT at an unreachable price then cancels it at tick 100 — 0 trades expected.

---

## Key Design Decisions

### Discovery-Driven, Config-Driven Tests
Instead of synthetic price data, this suite uses real market data windows identified by the Extreme Move Scanner. The Extreme Moves report provides time windows with guaranteed directional movement. These windows and their price levels are encoded in the scenario config JSON. Tests extract all expected values from the config at runtime via `ScenarioExpectedValues` — no hardcoded prices exist in the test file. Switching to different discovery windows only requires updating the config; tests adapt automatically.

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
