# Scenario Generator Tests Documentation

## Overview

Tests for the scenario generator core logic: chronological block generation (`BlocksGenerator`). All tests run against mocked data — no file I/O, no external data dependencies.

**Location:** `tests/scenario_generator/`

**Run:** `pytest tests/scenario_generator/ -v`

---

## Test Structure

### Mocking Strategy

The generator creates its data dependencies internally. Tests use `unittest.mock.patch` to intercept:

| Dependency | Generator | Mock Behavior |
|-----------|-----------|--------------|
| `DataCoverageReport` | BlocksGenerator | Provides gaps, start/end times |
| `TickIndexManager` | BlocksGenerator | `build_index()` → no-op |

### Fixtures (`conftest.py`)

- `generator_config` — `GeneratorConfig` with 4h blocks, 1h minimum for fast tests
- `make_gap(start, end, category)` — Creates `Gap` test objects
- `make_period(start, regime, session, ...)` — Creates `VolatilityPeriod` test objects
- `make_continuous_periods(start, hours, ...)` — Creates N consecutive hourly periods
- `mock_coverage_report(start, end, gaps)` — Configured mock `DataCoverageReport`

---

## test_blocks_generator.py

### Region Extraction (`_extract_continuous_regions`)
- No gaps → single region (with `preceding_gap=None`)
- SMALL/SHORT gaps → ignored (no split)
- WEEKEND gap → allowed, no split (professional platform behavior)
- MODERATE/LARGE gaps → region split (with `preceding_gap` tracking)
- Multiple gaps → only non-allowed gaps split (weekend gaps span across)
- Gap at data start → region starts after gap (with `preceding_gap` set)

### Constrained Blocks (no sessions)
- Full blocks generated correctly
- Short last block (≥ minimum) → generated
- Remainder below minimum → skipped
- Region below min_block_hours → no blocks
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

### Data Quality Warnings
- Data-start warning: first block at data begin → warning logged
- Post-gap warning: block after MODERATE gap → warning logged
- No post-gap warning within continuous region

### Session Utilities
- Session window extraction (allowed/mixed/no match)
- Session start point detection (transition/no transition)

### Edge Cases
- Gap covering all data → ValueError
- Candidate field values (symbol, broker_type, estimated_ticks=0)
