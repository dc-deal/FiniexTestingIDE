# Margin Validation Tests Documentation

## Overview

The margin validation test suite validates margin exhaustion, recovery, order rejection, and edge case handling. It uses a dedicated decision logic (`BacktestingMarginStress`) that intentionally exhausts margin, triggers rejections, recovers margin via explicit closes, and retries previously failed orders.

**Test Configurations:**

| Config | Balance | Max Ticks | Purpose |
|--------|---------|-----------|---------|
| `backtesting/margin_validation_test.json` | 80,000 JPY | 10,000 | Margin exhaustion, recovery, lot validation |
| `backtesting/margin_validation_zero_balance_test.json` | 0 JPY | 1,000 | Zero balance — all orders rejected |

**Common Settings:**
- Symbol: USDJPY
- Broker: MT5 (Vantage), Leverage 500
- Account Currency: JPY (auto-detected)
- Seeds: api_latency=42424, market_execution=98765

**Total Tests:** ~42

**Location:** `tests/margin_validation/`

---

## Margin Exhaustion Scenario

The test scenario is designed to produce a specific sequence of outcomes:

```
Tick  100: Open LONG #0 (1.0 lot)  → SUCCESS   margin_used ≈ 28,800 JPY
Tick  200: Open LONG #1 (1.0 lot)  → SUCCESS   margin_used ≈ 57,600 JPY, free ≈ 22,400
Tick  400: Open LONG #2 (1.0 lot)  → REJECTED  needs 28,800 > 22,400 free
Tick  600: Open LONG 0.001 lots    → REJECTED  below volume_min (0.01)
Tick  650: Open LONG 0.015 lots    → REJECTED  not aligned with volume_step (0.01)
Tick  700: Open LONG 200.0 lots    → REJECTED  above volume_max (100)
Tick  800: Close "FAKE_POS_999"    → ERROR     position not found (no crash)
Tick 5000: Close Trade #1          → SUCCESS   margin freed, free ≈ 50,000+
Tick 5200: Retry LONG (1.0 lot)    → SUCCESS   margin recovery confirmed
Tick 7200: Close Retry (hold_ticks expires)
Tick 8100: Close Trade #0 (hold_ticks expires)
```

**Expected execution statistics:**
- orders_sent: 7 (3 trade_sequence + 1 retry + 3 lot edge cases)
- orders_executed: 3 (trade #0, #1, retry)
- orders_rejected: 4 (trade #2 margin, lot_below_min, lot_step, lot_above_max)

---

## Test Structure

### Shared Test Architecture

The suite reuses shared test classes from `tests/shared/` and adds margin-specific tests. Shared tests that assume zero rejections (TestTradeExecution, TestLatencyDeterminism) are intentionally excluded.

```
tests/
├── shared/
│   ├── fixture_helpers.py         ← Scenario execution + extraction functions
│   ├── shared_pnl.py             ← TestPnLCalculation, TestTradeRecordCompleteness
│   ├── shared_warmup.py          ← TestWarmupValidation
│   ├── shared_tick_count.py      ← TestTickCount
│   ├── shared_latency.py         ← TestLatencyDeterminism (NOT used here)
│   └── shared_execution.py       ← TestTradeExecution (NOT used here)
├── margin_validation/
│   ├── conftest.py               ← MARGIN_VALIDATION_CONFIG = "backtesting/margin_validation_test.json"
│   ├── test_margin_validation.py ← Exhaustion, recovery, execution stats
│   ├── test_order_rejection.py   ← Lot validation, close errors, rejection tracking
│   ├── test_zero_balance.py      ← Zero balance scenario (own fixtures, separate config)
│   ├── test_pnl_calculation.py   ← Shared import
│   └── test_tick_count.py        ← Shared import
```

**Why exclude TestTradeExecution and TestLatencyDeterminism?**
- `TestTradeExecution` asserts `orders_rejected == 0` — this suite expects rejections
- `TestLatencyDeterminism.test_fill_tick_calculation` assumes all trade_sequence entries succeed — here trade #2 is rejected after latency

---

## Fixtures (conftest.py)

### Execution Fixtures

| Fixture | Scope | Description |
|---------|-------|-------------|
| `batch_execution_summary` | session | Runs margin validation scenario once per session |
| `process_result` | session | First scenario's ProcessResult |
| `tick_loop_results` | session | ProcessTickLoopResult with all execution data |
| `scenario_config` | session | Raw JSON config |

### Statistics Fixtures

| Fixture | Scope | Description |
|---------|-------|-------------|
| `portfolio_stats` | session | PortfolioStats (only successfully executed trades) |
| `backtesting_metadata` | session | BacktestingMetadata with expected_trades, warmup errors |
| `execution_stats` | session | ExecutionStats with sent/executed/rejected counts |

### Trade Data Fixtures

| Fixture | Scope | Description |
|---------|-------|-------------|
| `trade_history` | session | List of TradeRecord (only successful trades) |
| `trade_sequence` | session | Full trade sequence from config (including expected rejections) |
| `close_events` | session | Explicit close commands from config |
| `retry_events` | session | Retry orders from config |
| `edge_case_orders` | session | Edge case orders from config |

### Computed Fixtures

| Fixture | Scope | Description |
|---------|-------|-------------|
| `expected_successful_trades` | session | Count of trades expected to succeed (non-rejected sequence + retries) |
| `expected_rejections` | session | Count of expected rejections (margin + lot validation) |
| `expected_orders_sent` | session | Total open order attempts (excludes close_nonexistent) |

### Delay Generator Fixtures

| Fixture | Scope | Description |
|---------|-------|-------------|
| `seeds_config` | session | Seed configuration from trade_simulator_config |
| `api_delay_generator` | function | Fresh API delay generator per test |
| `exec_delay_generator` | function | Fresh execution delay generator per test |

---

## Test Files

### test_margin_validation.py (11 Tests)

Tests margin exhaustion, recovery after closing a position, and execution statistics accuracy.

#### TestMarginExhaustion

| Test | Description |
|------|-------------|
| `test_has_rejected_orders` | At least one order was rejected due to margin exhaustion |
| `test_rejection_count_matches_expected` | Rejected order count matches expected (margin + lot rejections) |
| `test_no_position_created_after_rejection` | Trade history only contains successfully opened trades |
| `test_successful_trades_count` | Portfolio total_trades matches expected successful opens |

#### TestMarginRecovery

| Test | Description |
|------|-------------|
| `test_retry_succeeded` | Retry after margin recovery produced a successful trade |
| `test_retry_has_order_id` | Successful retry has an order_id assigned |
| `test_total_trades_includes_retry` | Trade history includes both initial successful trades and retries |

#### TestExecutionStatistics

| Test | Description |
|------|-------------|
| `test_orders_sent_count` | orders_sent counts all open order attempts |
| `test_orders_executed_count` | orders_executed counts only successfully opened positions |
| `test_sent_equals_executed_plus_rejected` | orders_sent = orders_executed + orders_rejected |
| `test_trade_history_excludes_rejections` | Trade history length matches orders_executed |

---

### test_order_rejection.py (9 Tests)

Tests lot size validation, position close errors, and rejection tracking.

#### TestLotSizeValidation

| Test | Description |
|------|-------------|
| `test_lot_validation_rejections_counted` | Lot validation rejections included in orders_rejected |
| `test_invalid_lots_not_in_trade_history` | Trades with invalid lot sizes absent from trade history |
| `test_invalid_lots_not_in_expected_trades` | Expected trades don't contain rejected lot validation orders |
| `test_lot_step_misalignment_rejected` | Lot not aligned with volume_step (e.g., 0.015 with step 0.01) rejected |

#### TestPositionCloseErrors

| Test | Description |
|------|-------------|
| `test_scenario_completes_despite_close_error` | Scenario processes all 10,000 ticks despite close errors |
| `test_successful_trades_unaffected_by_close_error` | Successful trades unaffected by close_nonexistent error |

#### TestRejectionTracking

| Test | Description |
|------|-------------|
| `test_orders_sent_includes_rejected` | orders_sent > orders_executed when rejections occur |
| `test_rejected_orders_not_in_trade_history` | Rejected orders absent from trade history |
| `test_all_rejections_accounted_for` | Total rejections match expected count |

---

### test_zero_balance.py (6 Tests)

Tests that all orders are rejected when starting with zero balance. Uses a separate scenario config (`margin_validation_zero_balance_test.json`) with `initial_balance=0`. Module-scoped fixtures are defined within the test file itself (not in conftest.py).

#### TestZeroBalanceRejection

| Test | Description |
|------|-------------|
| `test_scenario_completes` | Scenario processes all 1,000 ticks despite all rejections |
| `test_all_orders_rejected` | Every order attempt rejected (2 trade_sequence entries) |
| `test_no_orders_executed` | Zero executed orders |
| `test_no_trades_in_history` | Trade history empty |
| `test_submitted_but_none_in_trade_history` | Orders submitted (PENDING) but all rejected at fill — none in trade_history |
| `test_orders_sent_equals_rejected` | orders_sent == orders_rejected (all fail) |

**Zero Balance Scenario:**

```
Tick  100: Open LONG  1.0  lot → REJECTED  insufficient margin (balance=0)
Tick  200: Open SHORT 0.01 lot → REJECTED  insufficient margin (balance=0)
```

**Expected execution statistics:**
- orders_sent: 2
- orders_executed: 0
- orders_rejected: 2

---

### test_pnl_calculation.py (16 Tests) — Shared

Imported from `tests/shared/shared_pnl.py`. Validates P&L calculations for successfully executed trades only.

#### TestPnLCalculation (13 Tests)

| Test | Description |
|------|-------------|
| `test_trade_count_matches` | Trade history count equals portfolio total_trades |
| `test_total_pnl_matches_portfolio` | Sum of trade net_pnl equals portfolio P&L |
| `test_total_spread_cost_matches` | Sum of spread costs matches portfolio total |
| `test_net_pnl_formula` | net_pnl = gross_pnl - total_fees |
| `test_total_fees_breakdown` | total_fees = spread + commission + swap |
| `test_gross_pnl_formula` | Gross P&L follows points × tick_value × lots formula |
| `test_exit_after_entry` | Exit tick after entry tick |
| `test_positive_lots` | Lot size positive |
| `test_spread_cost_positive` | Spread cost non-negative |
| `test_winning_losing_count` | Winner/loser counts match portfolio |
| `test_direction_counts` | Long/short counts match portfolio |
| `test_valid_prices` | Entry/exit prices positive |
| `test_valid_tick_value` | Tick value positive |

#### TestTradeRecordCompleteness (3 Tests)

| Test | Description |
|------|-------------|
| `test_all_required_fields_present` | position_id, symbol, direction, digits, contract_size populated |
| `test_timestamps_present` | Entry and exit timestamps present |
| `test_account_currency_present` | Account currency set |

---

### test_tick_count.py (4 Tests) — Shared

Imported from `tests/shared/shared_tick_count.py`.

#### TestTickCount

| Test | Description |
|------|-------------|
| `test_tick_count_matches_config` | Processed tick count equals max_ticks (10,000) |
| `test_decision_count_matches_ticks` | Decision logic called for every tick |
| `test_worker_call_count_matches_ticks` | Worker called for every tick |
| `test_tick_count_positive` | Tick count positive |

---

## Running the Tests

```bash
# Margin validation suite only (all configs)
pytest tests/margin_validation/ -v

# Specific test file
pytest tests/margin_validation/test_margin_validation.py -v

# Zero balance tests only
pytest tests/margin_validation/test_zero_balance.py -v

# Run scenarios without tests (for debugging)
python python/cli/strategy_runner_cli.py run backtesting/margin_validation_test.json
python python/cli/strategy_runner_cli.py run backtesting/margin_validation_zero_balance_test.json
```

**VS Code:** Use launch configurations:
- `🧩 Pytest: Margin Validation (All)` — run all margin tests (including zero balance)
- `🧪 Run (MARGIN_VALIDATION Scenario)` — run main scenario only
- `🧪 Run (ZERO_BALANCE Scenario)` — run zero balance scenario only

---

## Architecture Notes

### Decision Logic: BacktestingMarginStress

Located at `python/framework/decision_logic/core/backtesting/backtesting_margin_stress.py`.

Extends the multi-position pattern with four config-driven event types:

| Event Type | Config Key | Description |
|------------|------------|-------------|
| `trade_sequence` | `trade_sequence` | Standard open orders, optionally with `expect_rejection: true` |
| `close_events` | `close_events` | Explicit close by `sequence_index` at a specific tick |
| `retry_events` | `retry_events` | Open orders after margin recovery |
| `edge_case_orders` | `edge_case_orders` | Invalid operations (lot validation, close non-existent) |

### Margin Calculation Formula

For USDJPY (margin_currency=USD, quote_currency=JPY):
```
margin_required = (lots × contract_size × price) / leverage
                = (1.0 × 100,000 × 144.0) / 500
                = 28,800 JPY
```

### What Is NOT Tested Here

- **Hedging margin** — out of scope (covered by multi_position suite)
- **Multi-symbol margin** — architecturally impossible (one symbol per scenario)
- **Stop-out level** — not implemented in current framework
- **Zero balance scenarios** — ✅ covered by `test_zero_balance.py` (separate config)

### Key Data Flow

```
BacktestingMarginStress.compute()
  ├→ trade_sequence entries     → send_order() → margin check → accept/reject
  ├→ edge_case_orders           → send_order() → lot validation → reject
  │                             → close_position() → not found → error
  ├→ close_events               → close_position() → margin freed
  └→ retry_events               → send_order() → margin check → accept

Results available via:
  ├→ execution_stats.orders_rejected     (all rejection types)
  ├→ backtesting_metadata.expected_trades (successful opens only)
  └→ trade_history                        (closed trades only)
```
