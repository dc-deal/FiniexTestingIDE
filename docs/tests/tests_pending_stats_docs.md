# Pending Order Statistics Tests Documentation

## Overview

The pending stats test suite validates the pending order statistics system — latency tracking, outcome counting, synthetic close path, and force-closed anomaly detection.

**Test Configuration:** `backtesting/pending_stats_validation_test.json`
- Symbol: USDJPY
- Account Currency: JPY (auto-detected)
- 2 trades: 1 normal (happy path), 1 late (force-closed)
- Seeds: api_latency=12345, market_execution=67890
- Max Ticks: 5,000

**Total Tests:** 12

**Location:** `tests/pending_stats/`

---

## Test Structure

```
tests/
├── shared/
│   ├── fixture_helpers.py           ← extract_pending_stats() added here
│   └── shared_pending_stats.py      ← Reusable test classes
├── pending_stats/
│   ├── __init__.py
│   ├── conftest.py                  ← PENDING_STATS_CONFIG = "backtesting/pending_stats_validation_test.json"
│   └── test_pending_stats.py        ← Imports shared test classes
```

---

## Fixtures (conftest.py)

| Fixture | Scope | Description |
|---------|-------|-------------|
| `batch_execution_summary` | session | Runs scenario once per session |
| `process_result` | session | First scenario ProcessResult |
| `tick_loop_results` | session | ProcessTickLoopResult with all data |
| `pending_stats` | session | PendingOrderStats extracted from tick loop |
| `portfolio_stats` | session | PortfolioStats for cross-validation |
| `scenario_config` | session | Raw JSON config for assertions |
| `trade_sequence` | session | Trade sequence from config |

---

## Test Classes

### TestPendingStatsBaseline (6 tests)
Validates that pending stats are correctly populated after scenario execution.

| Test | Validates |
|------|-----------|
| `test_pending_stats_exists` | Stats object exists and has resolved orders |
| `test_total_resolved_consistency` | total_resolved = filled + rejected + timed_out + force_closed |
| `test_no_rejected_orders` | No rejections in normal backtesting |
| `test_no_timed_out_orders` | No timeouts in simulation mode |
| `test_latency_stats_populated` | avg/min/max latency ticks are set |
| `test_latency_avg_in_range` | avg is between min and max |

### TestSyntheticCloseNotCounted (1 test)
Validates that end-of-scenario position closes via synthetic orders do NOT produce false force-closed counts.

| Test | Validates |
|------|-----------|
| `test_filled_count_matches_trade_lifecycle` | filled count >= completed trades (no inflated force-closed) |

### TestForceClosedDetection (5 tests)
Validates that genuine stuck-in-pipeline orders are correctly detected as force-closed anomalies.

| Test | Validates |
|------|-----------|
| `test_force_closed_count` | At least 1 force-closed from late trade |
| `test_anomaly_records_populated` | Anomaly records exist for force-closed orders |
| `test_anomaly_record_has_reason` | Each anomaly record has a reason field |
| `test_anomaly_reason_is_scenario_end` | Reason is "scenario_end" for end-of-run force-close |
| `test_anomaly_record_has_latency` | Force-closed records have latency information |

---

## Scenario Design

The test scenario is specifically designed to trigger both code paths:

**Trade 1 (Happy Path):**
- Opens at tick 10, holds 100 ticks, closes at tick 110
- Both open and close orders flow through the latency pipeline normally
- Validates: filled counts, latency stats

**Trade 2 (Force-Closed):**
- Opens at tick 4990, hold_ticks=3, close signal at tick 4993
- With ~5 tick latency, the close order is still in the pipeline when the scenario ends at tick 5000
- `close_all_remaining_orders()` closes the position via synthetic order (no pending created)
- `clear_pending()` catches the stuck close order and records it as FORCE_CLOSED with reason="scenario_end"
- Validates: force-closed detection, anomaly records, reason field

---

## Key Design Decisions

### Synthetic Close Path
End-of-scenario position closes use `create_synthetic_close_order()` which bypasses the latency pipeline entirely. This ensures:
- No false FORCE_CLOSED in statistics
- Portfolio P&L is correct (positions are properly closed)
- Only genuine stuck-in-pipeline orders appear as anomalies

### Reason Field
Each FORCE_CLOSED anomaly record includes a `reason` field (e.g., "scenario_end", "manual_abort") to distinguish the cause. This is critical for future stress tests where extreme latencies will produce more force-closed orders.

---

## Running the Tests

```bash
# Run only pending stats tests
pytest tests/pending_stats/ -v

# Run with output (shows scenario execution)
pytest tests/pending_stats/ -v -s
```
