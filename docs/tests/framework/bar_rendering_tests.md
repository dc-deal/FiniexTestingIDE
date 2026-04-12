# Bar Rendering Consistency Test

## Purpose

Verifies that **BarRenderer** (tick-by-tick) and **VectorizedBarRenderer** (pandas batch) produce identical output for the same input ticks.

Both renderers solve the same problem — converting raw ticks into OHLCV bars — but use fundamentally different approaches. This test suite ensures they remain semantically equivalent.

## Why This Matters

The project uses three rendering contexts:

| Context | Renderer | Mode |
|---------|----------|------|
| Backtesting | BarRenderer | Tick-by-tick streaming |
| Import Pipeline | VectorizedBarRenderer | Pandas batch (resample) |
| Live Trading | BarRenderer | Tick-by-tick streaming |

If the renderers diverge, backtesting results won't match imported data, and live trading signals could differ from historical analysis.

## What Is Tested

| Test | Description |
|------|-------------|
| `test_standard_ticks_short_timeframes` | M1-M30 consistency with standard tick sequence |
| `test_standard_ticks_long_timeframes` | H1/H4 with 5000 ticks (~4.2 hours) |
| `test_gap_handling_m5` | 15-minute gap produces no synthetic bars in either renderer |
| `test_gap_handling_m1` | 5-minute gap at M1 granularity |
| `test_boundary_ticks` | Ticks exactly on bar boundaries assigned correctly |
| `test_single_tick_per_bar` | One tick per bar: OHLC all equal |
| `test_all_timeframes_bar_count` | Bar count matches across all 7 timeframes (M1-D1) |
| `test_volume_aggregation` | Volume sums match, total equals input |
| `test_forex_zero_volume` | Zero-volume forex ticks handled consistently |

## Test Data

All tests use **synthetic tick generators** (no external data dependencies):

- `generate_ticks()` — deterministic sequence with configurable interval, price step, volume
- `generate_ticks_with_gap()` — sequence with a configurable time gap
- `generate_boundary_ticks()` — ticks precisely on/near bar boundaries

## How the Comparison Works

The test feeds the same synthetic ticks through both renderers:

- **BarRenderer**: Ticks are processed one-by-one via `update_current_bars()` — the production path used in `process_tick_loop.py`. Completed bars are collected from `get_bar_history()`, plus the current (last) bar.
- **VectorizedBarRenderer**: Ticks are converted to a DataFrame and processed via `render_all_timeframes()` using `pandas.resample()`.

For each bar, the test verifies exact match of:
- **Timestamp** (bar start time)
- **OHLC** values (with `rel=1e-10` tolerance)
- **Volume** (aggregated sum)
- **Tick count**

## Files

- `tests/framework/bar_rendering/conftest.py` — Synthetic tick generators and fixtures
- `tests/framework/bar_rendering/test_renderer_consistency.py` — Consistency test suite

## Running

```bash
pytest tests/framework/bar_rendering/ -v --tb=short
```

VS Code: **"Pytest: Bar Rendering (All)"** launch configuration.
