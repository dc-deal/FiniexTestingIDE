# Test Suite: Tick Processing Budget

**Location:** `tests/tick_processing_budget/`

---

## Purpose

Unit tests for the tick processing budget feature (`_apply_tick_budget()` in `SharedDataPreparator`). Validates the flag-based virtual clock algorithm, `is_clipped` flag correctness, `ClippingStats` correctness, and edge case handling.

Flag-based: all ticks are returned with `is_clipped=True/False`. Broker path sees every tick; algo path skips clipped ticks.

Pure dry-ground tests — synthetic tick data, no file I/O, no subprocesses.

---

## Test Classes

### TestVirtualClockFiltering (6 tests)
Core algorithm correctness with known tick sequences.

| Test | What |
|------|------|
| `test_budget_2ms_known_sequence` | 6 ticks, budget 2ms → all 6 returned, 4 algo + 2 clipped at expected positions |
| `test_budget_1ms_integer_spacing` | 1ms spacing = 1ms budget → all 10 returned, all `is_clipped=False` |
| `test_large_budget_clips_most` | Budget 5ms, 1ms spacing → all 10 returned, only 2 algo ticks |
| `test_first_tick_always_kept` | Single tick always `is_clipped=False` regardless of budget |
| `test_budget_preserves_ranges` | Ranges dict preserved through flagging |
| `test_counts_reflect_total_ticks` | Counts dict = total tick count, algo count matches `stats.ticks_kept` |

### TestDeterminism (2 tests)
Reproducibility guarantees.

| Test | What |
|------|------|
| `test_same_input_same_output` | Identical input + budget = identical stats |
| `test_different_budgets_different_results` | Different budgets produce different clipping |

### TestEdgeCases (5 tests)
Boundary conditions and data quality guards.

| Test | What |
|------|------|
| `test_empty_ticks` | Empty tick list → zero stats, budget recorded |
| `test_pre_v13_data_skips_filtering` | `collected_msc=0` → all ticks returned unchanged (no `is_clipped` flag) |
| `test_pre_v13_logs_warning` | Pre-V1.3.0 data triggers logger.warning |
| `test_sub_ms_budget_no_clipping` | Budget < 1.0ms with integer-ms data → all `is_clipped=False` |
| `test_symbol_not_in_ticks` | Unknown symbol → empty stats |

### TestClippingStats (4 tests)
`ClippingStats` dataclass correctness.

| Test | What |
|------|------|
| `test_clipping_rate_calculation` | Rate = ticks_clipped / ticks_total × 100 |
| `test_stats_sum_invariant` | kept + clipped = total (always) |
| `test_zero_clipping_rate_when_none_clipped` | 0 clipped → rate 0.0 |
| `test_budget_recorded_in_stats` | Budget value preserved in stats |

### TestFlagBasedSplit (5 tests)
`is_clipped` flag integrity and tick preservation.

| Test | What |
|------|------|
| `test_all_ticks_returned_with_flags` | All ticks returned, every tick has `is_clipped` key |
| `test_flag_values_match_virtual_clock` | Flag sequence matches expected virtual clock decisions |
| `test_original_tick_data_preserved` | bid, ask, collected_msc, time_msc unchanged by flagging |
| `test_tick_dicts_are_copies` | Flagged dicts are copies — original dicts not mutated |
| `test_algo_tick_count_equals_stats_kept` | Non-clipped count = `stats.ticks_kept`, clipped = `stats.ticks_clipped` |

---

## Running

```bash
# All tests
pytest tests/tick_processing_budget/ -v

# Single class
pytest tests/tick_processing_budget/test_tick_budget_filtering.py::TestVirtualClockFiltering -v

# Flag-based split tests only
pytest tests/tick_processing_budget/test_tick_budget_filtering.py::TestFlagBasedSplit -v
```

---

## Related

- [Tick Processing Budget Guide](../tick_processing_budget_guide.md) — feature documentation
- [Inter-Tick Interval Tests](inter_tick_interval_tests.md) — P5/P95 interval computation tests
- [collected_msc Import Tests](tests_import_pipeline_docs.md) — data pipeline tests for collected_msc
