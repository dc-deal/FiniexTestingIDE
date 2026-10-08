# Pending-Order Counter Tests Documentation

## Overview

The pending-order counters say how each submission left its in-flight phase — accepted, rejected,
never confirmed, or expired on the way — and how long the venue took to answer. They are not
counted while a run goes: the pending-orders report derives them from the run's order-event stream
(#362). This suite runs one backtest and holds the row derived from its stream to what the scenario
did. The derivation itself, over hand-built event lists, is in the
[reporting tests](../framework/reporting_tests.md) (`test_pending_orders_report.py`); a live
session's counters are in the [order-event tests](../framework/order_events_tests.md).

**Test Configuration:** `backtesting/pending_stats_validation_test.json`
- Symbol: USDJPY
- Account Currency: JPY
- 2 trades: 1 normal (happy path), 1 whose entry the data's end meets on its way
- Seeds: inbound_latency=12345
- Max Ticks: 5,000

**Location:** `tests/simulation/pending_stats/`

---

## Test Structure

```
tests/
├── shared/
│   └── shared_pending_stats.py      ← Reusable test classes
├── simulation/pending_stats/
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
| `pending_row` | session | The scenario's pending-orders row, derived from its order events |
| `portfolio_stats` | session | PortfolioStats for cross-validation |
| `scenario_config` | session | Raw JSON config for assertions |
| `trade_sequence` | session | Trade sequence from config |

---

## Test Classes

### TestPendingCountersBaseline

| Test | Validates |
|------|-----------|
| `test_the_counters_are_populated` | The scenario submitted orders, so it has a row |
| `test_the_counters_add_up_to_the_submissions` | submitted = accepted + rejected + never confirmed + expired |
| `test_no_order_is_rejected` | No rejections in normal backtesting |
| `test_a_simulated_venue_confirms_everything` | No order is never confirmed, and none is listed |
| `test_answer_durations_are_populated` | avg/min/max in-flight durations are set |
| `test_the_average_lies_between_min_and_max` | avg is between min and max |

### TestEveryArrivalIsAnAcceptance

| Test | Validates |
|------|-----------|
| `test_accepted_covers_the_completed_trades` | Each completed trade's open and close were accepted — the scenario end closes no position (#492) |

### TestAnOrderTheDataEndMet

| Test | Validates |
|------|-----------|
| `test_it_is_counted_expired` | The late entry is counted expired on its way |
| `test_it_is_not_listed_as_unconfirmed` | The end of the data is no anomaly |

---

## Scenario Design

**Trade 1 (Happy Path):**
- Opens at tick 10, holds 100 ticks, closes at tick 110
- Both open and close orders flow through the latency pipeline and are accepted on arrival
- Validates: accepted counts, answer durations

**Trade 2 (an order the data's end meets on its way):**
- Signals its entry on tick 5000 — the scenario's last tick (`max_ticks` 5000)
- The open order is still in the latency pipeline when the scenario ends: no later tick can deliver
  it, so no position is opened
- `clear_pending()` hands the order back and it is booked `expired` with end reason `scenario_end`
- Validates: the expired count, and that it is not reported as unconfirmed

---

## Running the Tests

```bash
# Run only the pending-order counter tests
pytest tests/simulation/pending_stats/ -v

# Run with output (shows scenario execution)
pytest tests/simulation/pending_stats/ -v -s
```
