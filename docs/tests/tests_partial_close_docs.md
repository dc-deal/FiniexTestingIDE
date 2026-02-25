# Partial Close Test Suite Documentation (#119)

## Overview

The partial close test suite validates fractional position closing in the FiniexTestingIDE backtesting framework. It uses `BacktestingMultiPosition` with a `partial_close_sequence` parameter that triggers `close_position(position_id, lots=close_lots)` at configured tick numbers.

This suite proves that `PortfolioManager.partial_close_position()` and the routing logic in `AbstractTradeExecutor._fill_close_order()` correctly handle proportional P&L, fee splitting, lot tracking, and portfolio aggregation across multiple partial closes.

**Test Configuration:** `backtesting/partial_close_test.json`
- Symbol: USDJPY
- Account Currency: JPY (auto-detected)
- 2 trades: 1 LONG (0.03 lots, partially closed twice), 1 SHORT (0.02 lots, full close only)
- 2 partial close events at tick 2000 and 4000
- Seeds: api_latency=12345, market_execution=67890
- Max Ticks: 12,000

**Total Tests:** 39 (23 partial-close specific + 16 reused from baseline)

---

## Trade Sequence Design

The scenario creates one partially closed position and one isolated full-close position:

```
Tick:    0    100  200  2000       4000            8100       5200      12000
         |     |    |    |          |               |          |         |
Trade 0: ·     ██████████████████████████████████████  LONG  0.03 lots
                    ↓ partial      ↓ partial         ↓ full close (remainder)
                    0.01           0.01               0.01
Trade 1: ·          ██████████████████████████████████  SHORT 0.02 lots
         |     |    |    |          |               |          |         |
                                                               ↑ full close
```

### Partial Close Breakdown (Trade #0)

| Event | Tick | Close Lots | Remaining | Status |
|-------|------|-----------|-----------|--------|
| Open | ~105 | — | 0.03 | OPEN |
| Partial #1 | ~2005 | 0.01 | 0.02 | PARTIALLY_CLOSED |
| Partial #2 | ~4005 | 0.01 | 0.01 | PARTIALLY_CLOSED |
| Full close | ~8105 | 0.01 | 0.00 | CLOSED |

### Why This Sequence?

- **Trade #0** (LONG 0.03, two partials): Validates proportional P&L at different price levels, fee splitting across 3 records, and lot arithmetic (0.03 → 0.02 → 0.01 → 0.00)
- **Trade #1** (SHORT 0.02, no partial): Isolation check — proves partial close logic doesn't interfere with standard full closes

---

## Architecture: What Changed

### Core: PortfolioManager.partial_close_position()

New method in `python/framework/trading_env/portfolio_manager.py`:

| Aspect | Full Close | Partial Close |
|--------|-----------|---------------|
| P&L | Entire position | Proportional: `close_ratio = close_lots / position.lots` |
| Fees | All fees on position | Scaled: remaining fees `*= remaining_ratio` |
| Position | Deleted from `open_positions` | Mutated: reduced lots, PARTIALLY_CLOSED status |
| TradeRecord | Via `_create_trade_record()` | Built manually with `CloseType.PARTIAL` |
| `original_lots` | = lots (immutable) | Preserved across partial closes |

### Routing: AbstractTradeExecutor._fill_close_order()

Validation + routing in `python/framework/trading_env/abstract_trade_executor.py`:

1. `close_lots <= 0` → skip fill (log warning)
2. `close_lots > position.lots` → auto-convert to full close
3. `remaining < volume_min` → auto-convert to full close (with floating-point tolerance)
4. Otherwise → `partial_close_position()`

### Floating-Point Safety

Lot subtraction uses `round(position.lots - close_lots, 8)` to prevent IEEE 754 drift (e.g., `0.03 - 0.01 = 0.019999999999999997`). The volume_min comparison uses `1e-9` tolerance.

### Decision Logic: BacktestingMultiPosition Extension

New parameter `partial_close_sequence` (list of `{tick_number, position_index, close_lots}`). Processed in `_process_partial_closes()` between close-expired and open-new steps.

---

## Test Structure

### Shared Fixtures (`tests/shared/fixture_helpers.py`)

Same extraction logic as other suites. Suite-specific `conftest.py` adds `partial_close_sequence` fixture:

```python
PARTIAL_CLOSE_CONFIG = "backtesting/partial_close_test.json"
```

### Fixtures

| Fixture | Scope | Description |
|---------|-------|-------------|
| `batch_execution_summary` | session | Runs partial close scenario once per session |
| `process_result` | session | First scenario's ProcessResult |
| `tick_loop_results` | session | ProcessTickLoopResult with all execution data |
| `backtesting_metadata` | session | BacktestingMetadata |
| `portfolio_stats` | session | PortfolioStats with P&L, trade counts, costs |
| `trade_history` | session | List[TradeRecord] — 4 records (2 partial + 2 full) |
| `scenario_config` | session | Raw JSON config |
| `trade_sequence` | session | Configured trade specifications |
| `partial_close_sequence` | session | Configured partial close events |
| `seeds_config` | session | Seed values for deterministic latency |
| `api_delay_generator` | function | Fresh `SeededDelayGenerator` per test (`utils/seeded_generators/`) |
| `exec_delay_generator` | function | Fresh `SeededDelayGenerator` per test (`utils/seeded_generators/`) |

---

## Test Files

### test_partial_close.py (23 Tests)

Partial-close specific tests organized in 7 groups.

#### TestTradeRecordCount (5 Tests)

| Test | Description |
|------|-------------|
| `test_total_trade_count` | 4 trade records total (2 partial + 2 full) |
| `test_partial_record_count` | Exactly 2 records with CloseType.PARTIAL |
| `test_full_record_count` | Exactly 2 records with CloseType.FULL |
| `test_partial_records_share_position_id` | Both partials belong to same position |
| `test_partial_position_has_three_records` | 3 total records for partially closed position |

#### TestPartialCloseLots (4 Tests)

| Test | Description |
|------|-------------|
| `test_first_partial_lots` | First partial closes 0.01 lots |
| `test_second_partial_lots` | Second partial closes 0.01 lots |
| `test_remainder_lots` | Final full close is 0.01 lots |
| `test_lots_sum_equals_original` | Sum of all closed lots = 0.03 (original) |

#### TestPartialClosePnL (3 Tests)

| Test | Description |
|------|-------------|
| `test_each_partial_net_pnl_formula` | net_pnl = gross_pnl − total_fees per record |
| `test_partial_records_have_same_entry_price` | All records share identical entry price |
| `test_partial_records_have_different_exit_ticks` | Each partial at a different tick |

#### TestPartialCloseFeeSplitting (2 Tests)

| Test | Description |
|------|-------------|
| `test_each_partial_has_positive_fees` | Spread and total fees > 0 per partial |
| `test_fee_sum_across_partials_is_consistent` | Total spread for position is positive and non-negative per record |

#### TestPositionIsolation (3 Tests)

| Test | Description |
|------|-------------|
| `test_non_partial_has_full_close_type` | Trade #1 (SHORT) has CloseType.FULL |
| `test_non_partial_is_short` | Trade #1 direction is SHORT |
| `test_non_partial_lot_size` | Trade #1 closes with full 0.02 lots |

#### TestPortfolioAggregation (4 Tests)

| Test | Description |
|------|-------------|
| `test_portfolio_pnl_is_sum_of_trades` | Σ(trade.net_pnl) = portfolio total P&L |
| `test_portfolio_fees_is_sum_of_trade_fees` | Σ(trade.spread_cost) = portfolio spread cost |
| `test_total_trades_count` | portfolio.total_trades = 4 |
| `test_no_rejected_orders` | 0 rejected orders |

#### TestChronologicalOrder (2 Tests)

| Test | Description |
|------|-------------|
| `test_partial_closes_before_final` | Partial exit ticks < final full close tick |
| `test_partial_close_ticks_near_config` | Exit ticks ≈ configured tick_numbers (±15 latency) |

### test_pnl_calculation.py (16 Tests) — Reused from Baseline

Generic P&L validation from `tests/shared/shared_pnl.py`. Validates formulas, fee breakdowns, trade completeness across all 4 trade records.

---

## Running the Tests

```bash
# Partial close suite only
pytest tests/partial_close/ -v

# Specific test group
pytest tests/partial_close/test_partial_close.py::TestPartialCloseLots -v
```

**VS Code:** Use launch configuration `Pytest: Partial Close (All)`.

**Performance:** Full suite runs in ~3 seconds (scenario execution ~2.5s + 39 tests < 0.1s).

---

## Key Data Flow

```
BatchExecutionSummary
  └→ process_result_list[0]: ProcessResult
       └→ tick_loop_results: ProcessTickLoopResult
            ├→ portfolio_stats: PortfolioStats
            │    ├→ total_trades: 4  (2 partial + 2 full)
            │    ├→ total_profit / total_loss
            │    └→ total_spread_cost
            ├→ trade_history: List[TradeRecord]  ← 4 records
            │    ├→ [0] PARTIAL 0.01 lots (exit ~tick 2005)
            │    ├→ [1] PARTIAL 0.01 lots (exit ~tick 4005)
            │    ├→ [2] FULL    0.02 lots (exit ~tick 5205)  ← Trade #1
            │    └→ [3] FULL    0.01 lots (exit ~tick 8105)  ← Trade #0 remainder
            └→ execution_stats: ExecutionStats
                 ├→ orders_sent: 4  (2 open + 2 partial close orders)
                 └→ orders_rejected: 0
```
