# Pending Order Statistics Tests Documentation

## Overview

The pending stats test suite validates the pending order statistics system — latency tracking, outcome counting, and force-closed anomaly detection.

**Test Configuration:** `backtesting/pending_stats_validation_test.json`
- Symbol: USDJPY
- Account Currency: JPY
- 2 trades: 1 normal (happy path), 1 late (force-closed)
- Seeds: inbound_latency=12345
- Max Ticks: 5,000

**Location:** `tests/simulation/pending_stats/`

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

### TestPendingStatsBaseline
Validates that pending stats are correctly populated after scenario execution.

| Test | Validates |
|------|-----------|
| `test_pending_stats_exists` | Stats object exists and has resolved orders |
| `test_total_resolved_consistency` | total_resolved = filled + rejected + timed_out + force_closed |
| `test_no_rejected_orders` | No rejections in normal backtesting |
| `test_no_timed_out_orders` | No timeouts in simulation mode |
| `test_latency_stats_populated` | avg/min/max latency ticks are set |
| `test_latency_avg_in_range` | avg is between min and max |

### TestSyntheticCloseNotCounted
Validates that the end of the scenario does not inflate the force-closed count. The scenario end
closes no position (#492), so only an order genuinely stuck in the pipeline is force-closed. The
class name is older than that change.

| Test | Validates |
|------|-----------|
| `test_filled_count_matches_trade_lifecycle` | filled count >= completed trades (no inflated force-closed) |

### TestForceClosedDetection
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

**Trade 2 (open order stuck in the pipeline):**
- Signals its entry on tick 5000 — the scenario's last tick (`max_ticks` 5000)
- The open order is still in the latency pipeline when the scenario ends: no later tick can deliver
  it, so no position is opened
- `clear_pending()` catches the stuck OPEN order and records it as FORCE_CLOSED with reason="scenario_end"
- Validates: force-closed detection on the pipeline order, anomaly records, reason field

---

## Key Design Decisions

### No Close at the Scenario End
The scenario end closes no position (#492): a position still open is reported as open and valued,
not closed by an order the strategy never sent. So the scenario end adds nothing to the counters,
and only a genuinely stuck pipeline order appears as an anomaly.

### Reason Field
Each FORCE_CLOSED anomaly record includes a `reason` field (e.g., "scenario_end", "manual_abort") to distinguish the cause. This is critical for future stress tests where extreme latencies will produce more force-closed orders.

---

## Running the Tests

```bash
# Run only pending stats tests
pytest tests/simulation/pending_stats/ -v

# Run with output (shows scenario execution)
pytest tests/simulation/pending_stats/ -v -s
```
