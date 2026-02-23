# MVP Baseline Tests Documentation

## Overview

The MVP baseline test suite validates the core functionality of the FiniexTestingIDE backtesting framework. All tests run against a single scenario execution using a deterministic decision logic that triggers a predefined trade sequence.

**Test Configuration:** `backtesting/mvp_backtesting_validation_test.json`
- Symbol: USDJPY
- Account Currency: JPY (auto-detected)
- 3 trades: 2 LONG, 1 SHORT
- Seeds: api_latency=12345, market_execution=67890
- Max Ticks: 20,500

**Total Tests:** 48

**Location:** `tests/mvp_baseline/`

---

## Test Structure

### Shared Fixture Architecture

Fixture logic is shared across test suites via `tests/shared/fixture_helpers.py`. Each suite's `conftest.py` is a thin wrapper that specifies its config path and creates pytest fixtures from the shared helpers.

```
tests/
├── __init__.py
├── shared/
│   ├── __init__.py
│   └── fixture_helpers.py      ← Plain functions (no pytest decorators)
├── mvp_baseline/
│   ├── __init__.py
│   ├── conftest.py             ← MVP_CONFIG = "backtesting/mvp_backtesting_validation_test.json"
│   ├── test_bar_snapshots.py
│   ├── test_latency_determinism.py
│   ├── test_order_history.py
│   ├── test_pnl_calculation.py
│   ├── test_tick_count.py
│   ├── test_trade_execution.py
│   └── test_warmup_validation.py
└── multi_position/             ← Separate suite, same fixture pattern
    ├── conftest.py             ← MULTI_POSITION_CONFIG = "backtesting/multi_position_test.json"
    └── ...
```

**Why this pattern?**
- Adding a new test suite = one `conftest.py` with a different config path
- Test files are reusable across suites (fixture names are identical)
- Shared helpers are plain functions — easy to test and debug independently

---

## Fixtures (conftest.py)

The MVP baseline `conftest.py` wraps shared helpers from `tests/shared/fixture_helpers.py`. Each fixture calls the corresponding helper function with the MVP config path.

### Execution Fixtures

| Fixture | Scope | Description |
|---------|-------|-------------|
| `batch_execution_summary` | session | Runs MVP scenario once per session via `run_scenario()` |
| `process_result` | session | First scenario's ProcessResult from the batch |
| `tick_loop_results` | session | ProcessTickLoopResult containing all execution data |
| `scenario_config` | session | Raw JSON config loaded via `load_scenario_config()` |

### Statistics Fixtures

| Fixture | Scope | Description |
|---------|-------|-------------|
| `portfolio_stats` | session | PortfolioStats with P&L, trade counts, and cost breakdown |
| `backtesting_metadata` | session | BacktestingMetadata with snapshots, warmup errors, expected trades |

### Trade Data Fixtures

| Fixture | Scope | Description |
|---------|-------|-------------|
| `trade_history` | session | List of TradeRecord with full audit trail for each trade |
| `order_history` | session | List of OrderResult (executed + rejected orders) |
| `trade_sequence` | session | Expected trade sequence from decision logic config |

### Delay Generator Fixtures

| Fixture | Scope | Description |
|---------|-------|-------------|
| `seeds_config` | session | Seed configuration from trade_simulator_config |
| `api_delay_generator` | function | Fresh API delay generator per test (seed from config) |
| `exec_delay_generator` | function | Fresh execution delay generator per test (seed from config) |

---

## Test Files

### test_order_history.py (4 Tests)

Validates order history contents and consistency with execution statistics counters.

#### TestOrderHistoryBaseline

| Test | Description |
|------|-------------|
| `test_order_history_not_none` | order_history is populated and not empty after execution |
| `test_order_history_count_matches_stats` | Rejected entries == `orders_rejected`; executed entries >= `orders_executed` (close fills add extra entries beyond open fills) |
| `test_order_history_executed_have_price` | Every executed entry carries a positive `executed_price` |
| `test_order_history_rejection_reasons` | Every rejected entry carries a valid `RejectionReason` (trivially passes with 0 rejections) |

---

### test_bar_snapshots.py (7 Tests)

Tests bar rendering functionality by validating snapshots captured during the tick loop.

#### TestBarSnapshots

| Test | Description |
|------|-------------|
| `test_snapshot_count` | Verifies that 3 bar snapshots were captured as configured in `snapshot_checks` |
| `test_snapshots_not_empty` | Ensures each snapshot contains bar data |
| `test_snapshot_keys_format` | Validates snapshot key format matches pattern `{timeframe}_bar-{n}_tick{count}` |
| `test_snapshot_has_required_fields` | Checks each snapshot contains OHLC fields (open, high, low, close) and tick_count |
| `test_snapshot_ohlc_validity` | Validates OHLC logic: high ≥ max(open,close), low ≤ min(open,close) |
| `test_snapshot_tick_count_positive` | Ensures tick_count in each snapshot is positive |
| `test_snapshot_symbol_matches` | Verifies snapshot symbol matches scenario symbol (USDJPY) |

---

### test_latency_determinism.py (7 Tests)

Tests that order execution latency is deterministic and reproducible when using the same seeds.

#### TestLatencyDeterminism

| Test | Description |
|------|-------------|
| `test_api_delay_reproducible` | Generates API delay sequence twice with same seed, verifies identical results |
| `test_exec_delay_reproducible` | Generates execution delay sequence twice with same seed, verifies identical results |
| `test_different_seeds_different_sequences` | Confirms different seeds produce different delay sequences |
| `test_api_delay_within_bounds` | Validates API delays fall within configured min/max range |
| `test_exec_delay_within_bounds` | Validates execution delays fall within configured min/max range |
| `test_total_delay_calculation` | Verifies total delay equals API delay + execution delay |
| `test_fill_tick_calculation` | Confirms fill tick equals signal tick + total delay |

---

### test_pnl_calculation.py (16 Tests)

Validates profit/loss calculations through internal consistency checks using TradeRecord data.

#### TestPnLCalculation

| Test | Description |
|------|-------------|
| `test_trade_count_matches` | Trade history count equals portfolio total_trades |
| `test_total_pnl_matches_portfolio` | Sum of trade net_pnl equals portfolio total_profit - total_loss |
| `test_total_spread_cost_matches` | Sum of trade spread_cost equals portfolio total_spread_cost |
| `test_net_pnl_formula` | For each trade: net_pnl = gross_pnl - total_fees |
| `test_total_fees_breakdown` | For each trade: total_fees = spread_cost + commission_cost + swap_cost |
| `test_gross_pnl_formula` | Validates gross P&L formula: `points × tick_value × lots` where points = price_diff × 10^digits. This test caught a critical bug where SHORT positions used bid instead of ask for entry price |
| `test_exit_after_entry` | Exit tick index must be greater than entry tick index |
| `test_positive_lots` | Lot size must be positive for all trades |
| `test_spread_cost_positive` | Spread cost must be non-negative |
| `test_winning_losing_count` | Winners (net_pnl > 0) and losers (net_pnl ≤ 0) match portfolio counts |
| `test_direction_counts` | LONG and SHORT trade counts match portfolio totals |
| `test_valid_prices` | Entry and exit prices must be positive |
| `test_valid_tick_value` | Tick value must be positive |

#### TestTradeRecordCompleteness

| Test | Description |
|------|-------------|
| `test_all_required_fields_present` | Validates position_id, symbol, direction, digits, contract_size are populated |
| `test_timestamps_present` | Entry and exit timestamps must be present |
| `test_account_currency_present` | Account currency must be set for audit trail |

---

### test_tick_count.py (4 Tests)

Validates tick processing counts across different system components.

#### TestTickCount

| Test | Description |
|------|-------------|
| `test_tick_count_matches_config` | Processed tick count equals max_ticks from config (20,500) |
| `test_decision_count_matches_ticks` | Decision logic was called for every tick |
| `test_worker_call_count_matches_ticks` | Each worker was called for every tick |
| `test_tick_count_positive` | Tick count is positive |

---

### test_trade_execution.py (7 Tests)

Validates trade execution against the deterministic trade sequence.

#### TestTradeExecution

| Test | Description |
|------|-------------|
| `test_expected_trade_count` | Number of trades in sequence matches config (3 trades) |
| `test_executed_trade_count` | Portfolio executed all expected trades |
| `test_no_rejected_orders` | No orders were rejected during execution |
| `test_orders_sent_equals_executed` | All sent orders were executed |
| `test_trade_directions_match` | Executed trade directions match configured sequence (LONG, SHORT, LONG) |
| `test_trade_signal_ticks_match` | Trades were triggered at configured signal ticks (10, 6000, 13000) |
| `test_long_short_distribution` | Long/short counts match expected distribution (2 long, 1 short) |

---

### test_warmup_validation.py (3 Tests)

Validates that warmup data was correctly loaded before tick processing.

#### TestWarmupValidation

| Test | Description |
|------|-------------|
| `test_no_warmup_errors` | No warmup validation errors occurred |
| `test_warmup_errors_list_exists` | Warmup errors list is accessible (even if empty) |
| `test_has_warmup_errors_method` | BacktestingMetadata provides has_warmup_errors() method |

---

## Running the Tests

```bash
# MVP baseline suite only
pytest tests/mvp_baseline/ -v

# All test suites (baseline + multi-position)
pytest tests/ -v

# Specific test file
pytest tests/mvp_baseline/test_pnl_calculation.py -v
```

**VS Code:** Use launch configuration `🧪 Pytest (mvp_baseline)`.

---

## Architecture Notes

### Test Design Philosophy

The test suite uses **internal consistency validation** rather than independent recalculation. This approach:

1. Uses `TradeRecord` as single source of truth containing all calculation inputs
2. Validates mathematical relationships (e.g., net = gross - fees)
3. Cross-checks aggregations against portfolio statistics
4. Avoids complex external data dependencies

### Key Data Flow

```
BatchExecutionSummary
  └→ process_result_list[0]: ProcessResult
       └→ tick_loop_results: ProcessTickLoopResult
            ├→ portfolio_stats: PortfolioStats
            ├→ trade_history: List[TradeRecord]
            ├→ order_history: List[OrderResult]
            ├→ execution_stats: ExecutionStats
            ├→ decision_statistics: DecisionLogicStats
            └→ coordination_statistics: WorkerCoordinatorPerformanceStats
```

### TradeRecord Structure

Each `TradeRecord` contains full audit trail:
- Entry/exit prices, times, and tick indices
- Symbol properties (digits, contract_size)
- Fee breakdown (spread, commission, swap)
- Gross and net P&L
- Account currency for verification

This enables pen-and-paper verification of any trade's P&L calculation.