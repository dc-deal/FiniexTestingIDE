# Trend Channel Reference Test Suite (#118)

## Overview

Behavior validation for the didactic `CORE/trend_channel_reference` decision logic — the public
reference that drives the framework's **full order surface** (resting LIMIT and STOP entries,
its own SL/TP from submission, an always-on trailing stop, a partial-close ladder, multi-position
stacking).
The suite runs the logic end-to-end on two small mt5 EURUSD windows (one per entry mode) and
asserts the behaviors on the resulting trade history.

The robustness *aggregation* (distribution / IS-OOS / WFE) is covered separately by
[robustness_tests.md](robustness_tests.md); this suite asserts the *strategy behaviors*.

**Fixtures** (`tests/fixtures/scenario_sets/trend_channel_reference/`):

| Fixture set | Mode | Window | Drives |
|---|---|---|---|
| `trend_channel_reference_limit_fixture.json` | `limit_pullback` | EURUSD 2026-01-05 → 01-09 | resting LIMIT entries |
| `trend_channel_reference_stop_fixture.json` | `stop_breakout` | EURUSD 2026-01-12 → 01-16 | resting STOP entries |

Both windows produce stop and target exits, partial closes, and concurrent positions
(deterministic with the seeded sim latency). Their trailed positions ran on into their targets, so
the trail is driven tick by tick in its own file instead (`test_trend_channel_own_exits.py`).

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
| `test_no_orders_rejected` | capacity + gate guards keep `orders_rejected` and `orders_denied` at 0 in both runs |

### TestEntryModes

| Test | Description |
|---|---|
| `test_limit_mode_opens_via_limit_orders` | every entry in the limit fixture is `EntryType.LIMIT` |
| `test_stop_mode_opens_via_stop_orders` | every entry in the stop fixture is `EntryType.STOP` |

### TestRiskGeometry

| Test | Description |
|---|---|
| `test_the_levels_are_the_logic_s_own` | no trade carries a position level (`stop_loss` / `take_profit` empty), and every stop or target exit names the level that fired (`exit_level`, #625) |
| `test_sl_and_tp_triggers_both_occur` | `SL_TRIGGERED` and `TP_TRIGGERED` both present across the batch |

### TestPartialClose

| Test | Description |
|---|---|
| `test_position_closes_in_multiple_records` | a partial-close ladder closes one position across several `TradeRecord`s |
| `test_partial_portion_is_smaller_than_entry_size` | a closed portion is a fraction of the full entry size |

### TestMultiPosition

| Test | Description |
|---|---|
| `test_positions_stack_concurrently` | two distinct positions were open at the same time (tick-index overlap) |

### TestTheStrategyKeepsItsLastSetup

The STOP fixture runs with `write_strategy_files` on, the LIMIT fixture with it off.

| Test | Description |
|---|---|
| `test_the_stop_run_kept_its_last_setup` | the run holds `strategy_files/<scenario>/last_setup.json` with the mode, both bands, the gate and the entry geometry of the last submitted setup |
| `test_the_limit_run_with_it_off_kept_none` | with the parameter off, no strategy file is written |

---

## Tests (`test_trend_channel_own_exits.py`)

The logic driven tick by tick through its public surface — levels and initial risk handed in
through `restore_state`, three ticks through `execute_decision`, the stop read back through
`get_state_snapshot` — against a stand-in trading API. No batch, no data. The same pass tracks the
one entry the logic allows at a time, and two tests pin when it lets go of that slot.

| Test | Description |
|---|---|
| `test_the_trail_moves_the_own_stop_past_the_entry_and_the_stop_closes` | at 108 the own stop trails from 95 to 103 (past the 100 entry); at 104 it stays (it only tightens); at 102.9 the logic closes with `OwnExit(SL_TRIGGERED, 103.0)` |
| `test_a_restore_takes_back_only_the_positions_the_framework_restored` | a snapshot naming a position that did not come back gives back levels, initial risk and the taken rung only for the one that did |
| `test_an_entry_that_ended_without_a_position_frees_the_slot` | an entry that is neither a position nor resting nor in the submission pipeline has ended (refused, cancelled, or filled and closed before the pass saw it); the next armed setup is sent. Kept tracked, it blocked every later entry |
| `test_an_entry_still_on_its_way_keeps_the_slot` | while orders are in the submission pipeline an entry is neither resting nor a position yet, and no second entry goes out |

## Running the Tests

```bash
pytest tests/simulation/trend_channel_reference/ -v
```

**VS Code:** launch configuration `🧩 Pytest: Trend Channel Reference (All)`.

**Performance:** ~70s (two real ~5-day scenario windows; assertions are negligible).
