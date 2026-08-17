# Data Coverage Tests Documentation

## Overview

Covers the **data format version spans** of the data coverage report: the tick index records a
`data_format_version` per file, and grouping consecutive files by it turns that per-file field into
archive structure — which collector schema produced which period.

**Test Location:** `tests/framework/data_coverage/`
**Unit under test:** `python/framework/discoveries/data_coverage/data_format_version_spans.py`
**Total Tests:** 6

The unit is pure — it takes index entries and returns spans, with no index load and no file access.
Tests therefore need no fixture data beyond entry dicts.

---

## Test Files

### test_version_spans.py (6 tests)

**TestSpanGrouping:**
- Empty entry list → empty span list
- One file → one span carrying its own boundaries
- A contiguous run of one version collapses into a single span (first start → last end, counts summed)
- A version change opens a new span
- Interleaved versions (A-B-A from re-imports) stay three spans, never merged
- An entry without the version key reads `unknown` instead of raising

---

## Architecture Notes

- **Pure function, no IO.** The index load lives in the report's render path
  (`DataCoverageReport._version_spans_section`), so the grouping stays testable in isolation and the
  batch validation path never pays for an index it does not read.
- **No quality claim is tested, because none is made.** The version is an operator-set collector
  input describing the declared schema — it does not state how a field was obtained. Deriving
  "authentic vs. reconstructed timing" from it produces false statements on real archives, so the
  spans report structure only.
