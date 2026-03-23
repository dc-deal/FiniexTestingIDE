# Test Suite: Tick Processing Budget

**Location:** `tests/tick_processing_budget/`

---

## Purpose

Unit tests for the tick processing budget feature (`_apply_tick_budget()` in `SharedDataPreparator`). Validates the virtual clock filtering algorithm, `ClippingStats` correctness, and edge case handling.

Pure dry-ground tests — synthetic tick data, no file I/O, no subprocesses.

---

## Test Classes

### TestVirtualClockFiltering (6 tests)
Core algorithm correctness with known tick sequences.

| Test | What |
|------|------|
| `test_budget_2ms_known_sequence` | 6 ticks, budget 2ms → exactly 4 kept at expected positions |
| `test_budget_1ms_integer_spacing` | 1ms spacing = 1ms budget → all ticks pass |
| `test_large_budget_clips_most` | Budget 5ms, 1ms spacing → only every 5th tick survives |
| `test_first_tick_always_kept` | Single tick always passes regardless of budget |
| `test_budget_preserves_ranges` | Ranges dict preserved through filtering |
| `test_counts_match_kept_ticks` | Counts dict matches actual filtered tick count |

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
| `test_pre_v13_data_skips_filtering` | `collected_msc=0` → all ticks kept, no filtering |
| `test_pre_v13_logs_warning` | Pre-V1.3.0 data triggers logger.warning |
| `test_sub_ms_budget_no_clipping` | Budget < 1.0ms with integer-ms data → 0 clipped |
| `test_symbol_not_in_ticks` | Unknown symbol → empty stats |

### TestClippingStats (4 tests)
`ClippingStats` dataclass correctness.

| Test | What |
|------|------|
| `test_clipping_rate_calculation` | Rate = ticks_clipped / ticks_total × 100 |
| `test_stats_sum_invariant` | kept + clipped = total (always) |
| `test_zero_clipping_rate_when_none_clipped` | 0 clipped → rate 0.0 |
| `test_budget_recorded_in_stats` | Budget value preserved in stats |

---

## Running

```bash
# All tests
pytest tests/tick_processing_budget/ -v

# Single class
pytest tests/tick_processing_budget/test_tick_budget_filtering.py::TestVirtualClockFiltering -v
```

---

## Related

- [Tick Processing Budget Guide](../tick_processing_budget_guide.md) — feature documentation
- [Inter-Tick Interval Tests](inter_tick_interval_tests.md) — P5/P95 interval computation tests
- [collected_msc Import Tests](tests_import_pipeline_docs.md) — data pipeline tests for collected_msc
