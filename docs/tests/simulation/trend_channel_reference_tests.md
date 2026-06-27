# Trend Channel Reference Test Suite (#118)

## Overview

Behavior validation for the didactic `CORE/trend_channel_reference` decision logic — the public
reference that drives the framework's **full order surface** (resting LIMIT and STOP entries,
SL/TP at submission, an always-on trailing stop, a partial-close ladder, multi-position stacking).
The suite runs the logic end-to-end on two small mt5 EURUSD windows (one per entry mode) and
asserts the behaviors on the resulting trade history.

The robustness *aggregation* (distribution / IS-OOS / WFE) is covered separately by
[robustness_tests.md](robustness_tests.md); this suite asserts the *strategy behaviors*.

**Fixtures** (`tests/fixtures/scenario_sets/trend_channel_reference/`):

| Fixture set | Mode | Window | Drives |
|---|---|---|---|
| `trend_channel_reference_limit_fixture.json` | `limit_pullback` | EURUSD 2026-01-05 → 01-09 | resting LIMIT entries |
| `trend_channel_reference_stop_fixture.json` | `stop_breakout` | EURUSD 2026-01-12 → 01-16 | resting STOP entries |

Both windows produce SL/TP, trailing, partial closes, and concurrent positions (deterministic with
the seeded sim latency).

---

## Fixtures (`conftest.py`)

| Fixture | Scope | Description |
|---|---|---|
| `limit_batch` / `stop_batch` | session | One `run_scenario()` per entry-mode fixture |
| `limit_process_result` / `stop_process_result` | session | First scenario's `ProcessResult` |
| `limit_trades` / `stop_trades` | session | `List[TradeRecord]` per mode |
| `all_trades` | session | Combined trade history across both modes |
| `limit_execution_stats` / `stop_execution_stats` | session | `ExecutionStats` per mode |

---

## Tests (`test_trend_channel_reference.py`)

### TestRunHealth

| Test | Description |
|---|---|
| `test_limit_run_succeeds_with_trades` | limit_pullback run succeeds and produces trades |
| `test_stop_run_succeeds_with_trades` | stop_breakout run succeeds and produces trades |
| `test_no_orders_rejected` | capacity + gate guards keep `orders_rejected == 0` in both runs |

### TestEntryModes

| Test | Description |
|---|---|
| `test_limit_mode_opens_via_limit_orders` | every entry in the limit fixture is `EntryType.LIMIT` |
| `test_stop_mode_opens_via_stop_orders` | every entry in the stop fixture is `EntryType.STOP` |

### TestRiskGeometry

| Test | Description |
|---|---|
| `test_every_position_has_sl_and_tp` | every trade carries a stop-loss and take-profit |
| `test_sl_and_tp_triggers_both_occur` | `SL_TRIGGERED` and `TP_TRIGGERED` both present across the batch |

### TestPartialClose

| Test | Description |
|---|---|
| `test_position_closes_in_multiple_records` | a partial-close ladder closes one position across several `TradeRecord`s |
| `test_partial_portion_is_smaller_than_entry_size` | a closed portion is a fraction of the full entry size |

### TestTrailingStop

| Test | Description |
|---|---|
| `test_trailing_stop_can_close_in_profit` | an `SL_TRIGGERED` trade closed in profit — only possible if trailing ratcheted the SL past breakeven (verified against `entry_price` per direction) |

### TestMultiPosition

| Test | Description |
|---|---|
| `test_positions_stack_concurrently` | two distinct positions were open at the same time (tick-index overlap) |

---

## Running the Tests

```bash
pytest tests/simulation/trend_channel_reference/ -v
```

**VS Code:** launch configuration `🧩 Pytest: Trend Channel Reference (All)`.

**Performance:** ~70s (two real ~5-day scenario windows; assertions are negligible).
