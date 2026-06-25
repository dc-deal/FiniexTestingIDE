# Scenario Generator Tests Documentation

## Overview

Tests for the scenario generator: the splitter layer (`BlocksSplit` / `VolatilitySplit` /
`ContinuousSplit` / `WalkForwardSplit` behind `SplitterFactory`), the shared region extraction
(`ContinuousRegionExtractor`), the `WindowMaterializer` (windows → runnable scenarios), and the
`WindowSetSerializer` (one-or-many windows → a merged scenario set). All tests run against mocked
data — no file I/O, no external data dependencies.

**Location:** `tests/data/scenario_generator/`

**Run:** `pytest tests/data/scenario_generator/ -v`

---

## Test Structure

### Mocking Strategy

The splitters create their data dependencies internally. Tests use `unittest.mock.patch` to intercept:

| Dependency | Patched in | Mock Behavior |
|-----------|-----------|--------------|
| `DataCoverageReport` | `blocks_split` | Provides gaps, start/end times |
| `TickIndexManager` | `blocks_split` | `build_index()` → no-op |
| `DataCoverageReportCache` | `abstract_profile_splitter` | Region report for profile splitters |
| `VolatilityProfileAnalyzerCache` | `abstract_profile_splitter` | Volatility periods |
| `DiscoveryCacheManager` | `abstract_profile_splitter` | Discovery fingerprints |

### Fixtures (`conftest.py`)

- `generator_config` — `GeneratorConfig` with 4h blocks, 1h minimum for fast tests
- `blocks_config` — `BlocksStrategyConfig` (4h blocks, 1h minimum)
- `make_gap(start, end, category)` — Creates `Gap` test objects
- `make_period(start, regime, session, ...)` — Creates `VolatilityPeriod` test objects
- `make_continuous_periods(start, hours, ...)` — Creates N consecutive hourly periods
- `mock_coverage_report(start, end, gaps)` — Configured mock `DataCoverageReport`

---

## test_blocks_split.py

### Region Extraction (`ContinuousRegionExtractor`)
- No gaps → single region (with `preceding_gap=None`)
- SMALL/SHORT gaps → ignored (no split)
- WEEKEND gap → allowed, no split (professional platform behavior)
- MODERATE/LARGE gaps → region split (with `preceding_gap` tracking)
- Multiple gaps → only non-allowed gaps split (weekend gaps span across)
- Gap at data start → region starts after gap (with `preceding_gap` set)
- Clip-to-range → regions clipped to requested `[start, end]`

### Constrained Blocks
- Full blocks generated correctly (WindowSet carries symbol/broker/strategy)
- Short last block (≥ minimum) → generated
- Remainder below minimum → skipped
- Region below min_block_hours → no blocks
- Blocks are consecutive (end == next start)
- `block_index` is a 0-based sequence

### Count Limiting
- count_max truncates excess blocks
- count_max above generated → all returned

### Data Quality Warnings
- Data-start warning: first block at data begin → warning logged
- Post-gap warning: block after MODERATE gap → warning logged
- No post-gap warning within continuous region

### Edge Cases
- Gap covering all data → ValueError
- Window field values (estimated_ticks=0, regime=MEDIUM, atr=0.0)

### Start / End Clipping
- `start_time` / `end_time` clip the blocks to the requested range (not the full coverage)
- No range given → blocks span the full data coverage (unchanged default)

### Gap-Aware Block Start
- A block boundary landing in a weekend/holiday snaps forward to the next market open
  (`MarketCalendar.next_market_open`, §37) → no window starts on a weekend day

---

## test_splitters.py

### SplitterFactory
- Every `GenerationStrategy` resolves to its splitter class (isinstance `AbstractSplitter`)
- No strategy member is missing from the registry

### WalkForwardSplit (extension-point stub, #32)
- `split()` raises `NotImplementedError` (rolling-fold algorithm lands with #32 Phase 4)

### ContinuousSplit / VolatilitySplit (mocked caches)
- Continuous: gap-free region → one window with period-derived regime
- Profile splitters require `start_time` / `end_time`
- Volatility: region ≤ max_block_hours → single `single_region` window
- Volatility: region > max_block_hours → splits, no window exceeds the max

---

## test_window_materializer.py

### Role assignment
- Robustness off → no roles; on → time-ordered IS then OOS (correct split)

### Scenario dicts (save path)
- No per-scenario cascade keys
- Role present when robustness enabled, absent otherwise
- Blocks naming is the 3-part `symbol_mode_NN` form

### Single scenarios (profile in-memory path)
- Each scenario gets the symbol's authoritative quote-currency balance (#265)
- Regime / session carried from the source window; `is_profile_run=True`
- `scenario_index` continuous from `start_index`; roles assigned when enabled

---

## test_window_set_serializer.py

The scenario-set assembler (`_build_scenario_set_config`) — multiple `WindowSet`s merged into one
runnable set. Tests the pure dict assembly (no file write); quote-currency resolution is patched.

- Multi-symbol merge → every symbol's scenarios present, names unique (symbol-prefixed)
- Robustness: IS/OOS roles assigned **per symbol** (each symbol's own trailing fraction is OOS —
  no cross-symbol future leak); top-level `robustness` block written
- No robustness → no `role` keys, no `robustness` block
- Quote-currency balance seeded into set-wide `global`, unioned across symbols (#265)

---

## test_balance_defaults.py

Quote-currency balance seeding + authoritative resolution from the broker config (#265):
`ensure_quote_balance` (no-clobber), `resolve_symbol_currencies` / `resolve_quote_currency`
(authoritative split), and the `SymbolCurrencyError` cases (missing / mismatched split).

---

## test_config_fingerprint_utils.py

**Location:** `tests/test_config_fingerprint_utils.py`

Tests for SHA256-based config fingerprinting used by discovery caches and profile freshness validation.

- Deterministic output for same input
- Different input → different fingerprint
- Key ordering irrelevant (sorted internally)
- Empty dict → valid 64-char hex
- Nested dicts → order-independent
- Returns valid hex string of correct length
