# Multi-Position Test Suite Documentation (#114)

## Overview

The multi-position test suite validates overlapping position management in the FiniexTestingIDE backtesting framework. It uses a dedicated decision logic (`BacktestingMultiPosition`) that opens multiple simultaneous positions, tests hedging (opposite directions on the same symbol), and validates selective per-position closing.

This suite proves that the engine's `TradeSimulator`, `PortfolioManager`, and `OrderLatencySimulator` correctly handle the full complexity of multi-position trading — a prerequisite for any real-world hedging strategy.

**Test Configuration:** `backtesting/multi_position_test.json`
- Symbol: USDJPY
- Account Currency: JPY (auto-detected)
- 4 trades: 3 LONG, 1 SHORT (overlapping)
- Peak concurrent positions: 3
- Seeds: api_latency=12345, market_execution=67890
- Max Ticks: 20,500

**Total Tests:** 65 (28 multi-position specific + 37 reused from baseline)

---

## Trade Sequence Design

The scenario creates a carefully designed overlapping trade pattern:

```
Tick:    0    2000   3000       7000  7500  8100        12000      15000     20500
         |     |      |          |     |     |            |          |         |
Trade 0: ████████████████████████████████████████  LONG  0.01 lots
Trade 1:       ███████████████████████████         LONG  0.02 lots
Trade 2:              ██████████████████           SHORT 0.01 lots
Trade 3:                                              ████████████  LONG  0.01 lots
         |     |      |          |     |     |     |      |          |
         0     1      3←peak     2     1     0     0      1          0  ← concurrent count
```

### Validation Windows

| Tick Range | Concurrent | Open Positions | Validates |
|------------|-----------|----------------|-----------|
| 100–2000 | 1 | #0 LONG | Single position baseline |
| 2000–3000 | 2 | #0 LONG + #1 LONG | Position stacking (same direction) |
| 3000–7000 | **3** | #0 LONG + #1 LONG + #2 SHORT | **Hedging** (opposite directions) |
| 7000–7500 | 2 | #0 LONG + #1 LONG | Selective close (#2 closed, others unaffected) |
| 7500–8100 | 1 | #0 LONG | Second selective close (#1 closed) |
| 8100–12000 | **0** | — | **Gap** (all closed, clean state) |
| 12000–15000 | 1 | #3 LONG | **Recovery** (fresh open after gap) |
| 15000–20500 | 0 | — | Final cleanup |

### Why This Sequence?

Each trade serves a specific validation purpose:

- **Trade #0** (LONG 0.01, hold 8000): Base position — longest running, overlaps with all first-group trades
- **Trade #1** (LONG 0.02, hold 5500): Stacking — opens while #0 is active, proves >1 concurrent works
- **Trade #2** (SHORT 0.01, hold 4000): Hedging — opposite direction while 2 LONGs open, closes first
- **Trade #3** (LONG 0.01, hold 3000): Recovery — opens after full gap, proves no state leakage

---

## Architecture: What Changed vs. MVP Baseline

### Decision Logic: BacktestingMultiPosition

The MVP baseline uses `BacktestingDeterministic` which has a fundamental limitation: only one position can be open at a time (`len(open_positions) == 0` guard), and FLAT means "close all".

`BacktestingMultiPosition` removes these constraints:

| Aspect | BacktestingDeterministic | BacktestingMultiPosition |
|--------|-------------------------|--------------------------|
| Active trades | Singular (`self.active_trade`) | Dict (`self._active_trades`) |
| Open guard | Blocks if position exists | Opens regardless |
| FLAT signal | Close everything | No new position (existing unchanged) |
| Close mechanism | Blanket close | Selective by `position_id` |
| Position tracking | None | `_position_map[seq_idx] → order_id` |
| Statistics | Standard | + `close_events`, `max_concurrent` |

### Engine Support (No Changes Needed)

The engine (`TradeSimulator`, `PortfolioManager`) already supported multi-position:
- `open_positions` is a Dict (not singular)
- `close_position_portfolio()` works with `position_id`
- No single-position guards exist in the engine
- Broker config `hedging_allowed: true` enables opposite-direction positions

The limitation was entirely in the decision logic layer.

---

## Test Structure

### Shared Fixtures (`tests/shared/fixture_helpers.py`)

Both the MVP baseline and multi-position suites share the same extraction logic. Suite-specific `conftest.py` files only differ in the config path:

```python
# tests/mvp_baseline/conftest.py
MVP_CONFIG = "backtesting/mvp_backtesting_validation_test.json"

# tests/multi_position/conftest.py
MULTI_POSITION_CONFIG = "backtesting/multi_position_test.json"
```

All fixture logic (run_scenario, extract_process_result, etc.) lives in `fixture_helpers.py` as plain functions, wrapped by each suite's `conftest.py` as `@pytest.fixture(scope="session")`.

### Fixtures

| Fixture | Scope | Description |
|---------|-------|-------------|
| `batch_execution_summary` | session | Runs multi-position scenario once per test session |
| `process_result` | session | First scenario's ProcessResult |
| `tick_loop_results` | session | ProcessTickLoopResult with all execution data |
| `backtesting_metadata` | session | BacktestingMetadata with expected_trades, warmup_errors |
| `portfolio_stats` | session | PortfolioStats with P&L, trade counts, costs |
| `trade_history` | session | List[TradeRecord] — full audit trail per trade |
| `scenario_config` | session | Raw JSON config for assertion values |
| `trade_sequence` | session | Configured trade specifications from JSON |
| `seeds_config` | session | Seed values for deterministic latency |
| `api_delay_generator` | function | Fresh SeededDelayGenerator per test |
| `exec_delay_generator` | function | Fresh SeededDelayGenerator per test |

---

## Test Files

### test_multi_position.py (28 Tests) — NEW

Multi-position specific tests organized in 6 groups.

#### TestConcurrentPositions (5 Tests)

Validates that multiple positions can be open simultaneously.

| Test | Description |
|------|-------------|
| `test_peak_concurrent_is_three` | Maximum 3 positions open at once (ticks ~3006–7006) |
| `test_two_concurrent_after_first_close` | After SHORT closes, 2 LONGs remain (tick ~7200) |
| `test_one_position_after_second_close` | After LONG #1 closes, 1 LONG remains (tick ~7800) |
| `test_zero_positions_in_gap` | No positions open in gap window (tick ~10000) |
| `test_more_than_one_position_existed` | Fundamental assertion: peak concurrency > 1 |

**How concurrency is computed:** Helper function `_concurrent_at_tick()` counts trades where `entry_tick_index ≤ tick < exit_tick_index`. This uses actual `TradeRecord` data from the portfolio — not internal decision logic state — validating the real engine behavior.

---

#### TestSelectiveClose (3 Tests)

Validates that positions close individually, not as a blanket operation.

| Test | Description |
|------|-------------|
| `test_trades_close_at_different_ticks` | All exit ticks are unique (no bulk close) |
| `test_close_order_matches_hold_ticks` | Exit ticks are monotonically ascending by expiry order |
| `test_close_tick_near_expected` | Actual exit tick ≈ signal_tick + hold_ticks (±15 ticks latency tolerance) |

**Trade matching:** Uses triple constraint `direction + lot_size + entry_tick proximity` to uniquely identify trades — necessary because Trade #0 and #3 share identical direction and lot size.

---

#### TestHedging (3 Tests)

Validates opposite-direction positions on the same symbol simultaneously.

| Test | Description |
|------|-------------|
| `test_has_both_directions` | Trade history contains both LONG and SHORT trades |
| `test_opposite_directions_overlap` | At least one LONG and one SHORT have overlapping time windows |
| `test_hedging_window_has_three_positions` | At tick 5000: exactly 2 LONGs + 1 SHORT open |

**Broker requirement:** MT5 broker config must have `hedging_allowed: true` and `margin_mode: retail_hedging`. Without this, opposite-direction orders would be rejected or net out.

---

#### TestPositionIsolation (6 Tests)

Validates per-position P&L correctness and portfolio aggregation.

| Test | Description |
|------|-------------|
| `test_unique_position_ids` | Every trade has a unique `position_id` (no ID leaking) |
| `test_per_trade_net_pnl_formula` | net_pnl = gross_pnl − total_fees for each trade independently |
| `test_portfolio_pnl_is_sum_of_trades` | Σ(trade.net_pnl) = portfolio total P&L |
| `test_portfolio_fees_is_sum_of_trade_fees` | Σ(trade.spread_cost) = portfolio total_spread_cost |
| `test_all_trades_have_valid_symbol` | All trades on USDJPY (single symbol) |
| `test_each_trade_has_positive_fees` | No negative spread_cost or total_fees |

**Core assertion:** `test_portfolio_pnl_is_sum_of_trades` is the most important aggregation test. If per-position P&L isolation is broken, this test catches it — the sum of individual parts must equal the whole.

---

#### TestRecoveryAfterGap (3 Tests)

Validates clean position opening after all previous positions are closed.

| Test | Description |
|------|-------------|
| `test_gap_exists_between_groups` | 0 concurrent positions exist between first group close and recovery open |
| `test_recovery_trade_is_independent` | Trade #3 does not overlap with any earlier trade |
| `test_total_trade_count` | All 4 configured trades were executed (including recovery) |

**Why this matters:** If the decision logic leaks state from closed positions (e.g., stale entries in `_active_trades`), recovery trades could fail silently. This group catches state contamination.

---

#### TestMultiPositionMetadata (8 Tests)

Validates BacktestingMetadata tracking from the decision logic.

| Test | Description |
|------|-------------|
| `test_expected_trades_count` | Metadata has 4 expected trades matching config |
| `test_expected_trades_have_order_ids` | Each expected trade was assigned an order_id |
| `test_expected_trades_directions_match_config` | Directions in metadata match config sequence |
| `test_expected_trades_signal_ticks_match` | Signal ticks in metadata match config tick_numbers |
| `test_order_ids_match_trade_history` | Metadata order_ids = trade_history position_ids (pipeline integrity) |
| `test_no_warmup_errors` | No warmup validation errors |
| `test_no_rejected_orders` | All orders executed (0 rejections) |
| `test_tick_count_matches_config` | 20,500 ticks processed |

**Pipeline integrity:** `test_order_ids_match_trade_history` validates the full chain: decision_logic → send_order() → order_id → portfolio → position_id → TradeRecord. If any step drops or corrupts an ID, this test catches it.

---

### Reused Tests from MVP Baseline (37 Tests)

These test files are identical to the MVP baseline suite. They validate generic properties that must hold for ANY scenario — single or multi-position.

| File | Tests | Validates |
|------|-------|-----------|
| `test_pnl_calculation.py` | 16 | P&L formulas, fee breakdown, aggregation, trade completeness |
| `test_trade_execution.py` | 7 | Trade count, directions, signal ticks, order execution |
| `test_latency_determinism.py` | 7 | Seeded delays reproducible, within bounds, total delay formula |
| `test_tick_count.py` | 4 | Tick count matches config, decision/worker call counts |
| `test_warmup_validation.py` | 3 | No warmup errors, errors list accessible |

**Not reused:** `test_bar_snapshots.py` — the multi-position config has empty `bar_snapshot_checks` (bar validation is not the focus of this suite), so `test_snapshots_not_empty` would fail.

---

## Running the Tests

```bash
# Multi-position suite only
pytest tests/multi_position/ -v

# MVP baseline suite only
pytest tests/mvp_baseline/ -v

# All test suites
pytest tests/ -v

# Specific test group
pytest tests/multi_position/test_multi_position.py::TestHedging -v
```

**VS Code:** Use launch configuration `🧪 Pytest (multi_position)`.

**Performance:** Full suite runs in ~2–4 seconds (scenario execution ~2s + 65 tests < 0.1s).

---

## Key Data Flow

```
BatchExecutionSummary
  └→ process_result_list[0]: ProcessResult
       └→ tick_loop_results: ProcessTickLoopResult
            ├→ portfolio_stats: PortfolioStats
            │    ├→ total_trades: 4
            │    ├→ total_profit / total_loss
            │    └→ total_spread_cost
            ├→ trade_history: List[TradeRecord]  ← 4 records with full audit trail
            ├→ execution_stats: ExecutionStats
            │    ├→ orders_sent: 4
            │    └→ orders_rejected: 0
            └→ decision_statistics: DecisionLogicStats
                 └→ backtesting_metadata: BacktestingMetadata
                      ├→ expected_trades: [{signal_tick, direction, order_id, ...}, ...]
                      ├→ warmup_errors: []
                      └→ tick_count: 20500
```

---

## Verified Results (Reference Run)

From the initial validation run (2026-02-14):

```
Trade History:
  # |  Dir  |  Lots | Entry Tick | Exit Tick | Duration | Gross P&L |  Fees | Net P&L
  1 | LONG  |  0.01 |        105 |      8105 |     8000 |    +0.77  |  0.13 |  +0.64
  2 | LONG  |  0.02 |       2006 |      7504 |     5498 |    -0.96  |  0.40 |  -1.36
  3 | SHORT |  0.01 |       3006 |      7006 |     4000 |    -0.78  |  0.13 |  -0.91
  4 | LONG  |  0.01 |      12007 |     15008 |     3001 |    -0.37  |  0.14 |  -0.51

Portfolio: 4 trades (1W/3L) | P&L: -¥2.14 | Spread: ¥0.80
Peak concurrent: 3 | Max drawdown: -¥2.49
```
