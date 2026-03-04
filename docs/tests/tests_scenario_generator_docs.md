# Scenario Generator Tests Documentation

## Overview

Tests for the scenario generator core logic: chronological block generation (`BlocksGenerator`) and high-volatility scenario selection (`HighVolatilityGenerator`). All tests run against mocked data — no file I/O, no external data dependencies.

**Location:** `tests/scenario_generator/`

**Run:** `pytest tests/scenario_generator/ -v`

---

## Test Structure

### Mocking Strategy

Both generators create their data dependencies internally. Tests use `unittest.mock.patch` to intercept:

| Dependency | Generator | Mock Behavior |
|-----------|-----------|--------------|
| `DataCoverageReport` | BlocksGenerator | Provides gaps, start/end times |
| `TickIndexManager` | BlocksGenerator | `build_index()` → no-op |
| `MarketAnalyzer` | HighVolatilityGenerator | Returns period lists |

### Fixtures (`conftest.py`)

- `generator_config` — `GeneratorConfig` with short durations (2h warmup, 4h blocks) for fast tests
- `make_gap(start, end, category)` — Creates `Gap` test objects
- `make_period(start, regime, session, ...)` — Creates `PeriodAnalysis` test objects
- `make_continuous_periods(start, hours, ...)` — Creates N consecutive hourly periods
- `mock_coverage_report(start, end, gaps)` — Configured mock `DataCoverageReport`
- `mock_analyzer(high_vol_periods, all_periods)` — Configured mock `MarketAnalyzer`

---

## test_blocks_generator.py

### Region Extraction (`_extract_continuous_regions`)
- No gaps → single region
- SMALL/SHORT gaps → ignored (no split)
- WEEKEND/MODERATE/LARGE gaps → region split
- Multiple gaps → correct region count
- Gap at data start → region starts after gap

### Constrained Blocks (no sessions)
- Full blocks generated correctly
- Short last block (≥ minimum) → generated
- Remainder below minimum → skipped
- Region too short for warmup → no blocks
- Blocks are consecutive (end == next start)

### Constrained Blocks (with sessions, extend=false)
- Only generates blocks within allowed session windows

### Extended Blocks (with sessions, extend=true)
- Blocks start at session transition points
- Blocks run full duration past session boundary
- No session start in region → no blocks

### Count Limiting
- count_max truncates excess blocks
- count_max above generated → all returned

### Warmup Handling
- First block starts after warmup offset
- Warmup reapplied after each gap

### Session Utilities
- Session window extraction (allowed/mixed/no match)
- Session start point detection (transition/no transition)

### Edge Cases
- Gap covering all data → ValueError
- Candidate field values (symbol, broker_type, estimated_ticks=0)

---

## test_high_volatility_generator.py

### Scenario Centering
- Centered on high-vol period with ±block_hours/2
- Hour alignment (no sub-hour drift)

### Validation Checks
- **Insufficient warmup** — period too close to data start → skip
- **Low quality** — real_bar_ratio below threshold → skip
- **Good quality** — ratio above threshold → accepted
- **Gap in window** — missing period → skip
- **Continuous window** — all periods present → accepted
- **Overlap** — adjacent high-vol periods → second skipped
- **Non-overlapping** — separated periods → both accepted

### _has_overlap() Unit Tests
- Before, after, adjacent (touching) → no overlap
- Partial overlap → detected
- Empty used_ranges → no overlap

### _check_gap_in_window() Unit Tests
- Continuous periods → None
- Gap between periods → detected
- Gap at window start/end → detected
- No periods in window → error

### Full Generation Flow
- No HIGH/VERY_HIGH periods → ValueError
- Fewer valid than requested → returns what's available
- Candidate fields match source period
- Stops at count limit
