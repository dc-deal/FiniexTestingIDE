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

## Price Basis (`test_price_basis.py`)

A bar is rendered from what its venue actually trades: the traded price where trades print
centrally, the bid/ask midpoint where they do not. Three properties are pinned, and the first
is the whole safety argument for re-rendering the archive.

**It is a no-op on every file on disk today.** Kraken carries `last == bid == ask` below
collector format 1.6.0, MT5 carries `last == 0.0` on every row — so both resolve to exactly
what the midpoint produced before. Verified against real production files: `price == mid` on
100 % of rows for both brokers. Only a tick whose bid and ask differ makes the two diverge.

**A quote-driven venue must never render at zero.** MT5 reports `last = 0.0` because it has no
central place where trades happen. Nothing asserted that a bar price is positive before, and
`0 >= 0` satisfies the existing `high >= low` — so a misfiring gate would have produced an
archive of zeros in silence. Both venues are checked.

**The renderer refuses an unnormalized frame.** It is a pure transformation running in a
worker pool, so it does not resolve the basis itself — resolving there would mean one config
read per process and a second copy of the rule. `read_tick_parquet` derives the `price` column
and every consumer inherits it; a frame that bypassed the reader is refused by name.

## Files

- `tests/framework/bar_rendering/conftest.py` — Synthetic tick generators and fixtures
- `tests/framework/bar_rendering/test_renderer_consistency.py` — Consistency test suite

## Running the Tests

```bash
pytest tests/framework/bar_rendering/ -v --tb=short
```

VS Code: **"Pytest: Bar Rendering (All)"** launch configuration.
