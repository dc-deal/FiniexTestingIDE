# Data Coverage Tests Documentation

## Overview

Covers the pure pieces of the data coverage report. The **data format version spans**: the tick
index records a `data_format_version` per file, and grouping consecutive files by it turns that
per-file field into archive structure — which collector schema produced which period. The **file
attribution** of a gap: which file holds the data on either side of it. And the **extent** of a gap
as the report renders it.

**Test Location:** `tests/framework/data_coverage/`
**Units under test:** `python/framework/discoveries/data_coverage/data_format_version_spans.py`,
`gap_file_attribution.py` beside it, and the extent rendering of `DataCoverageReport`.
The units are pure — they take index entries or gaps, with no index load and no file access.
Tests therefore need no fixture data beyond entry dicts.

---

## Test Files

### test_version_spans.py

**TestSpanGrouping:**
- Empty entry list → empty span list
- One file → one span carrying its own boundaries
- A contiguous run of one version collapses into a single span (first start → last end, counts summed)
- A version change opens a new span
- Interleaved versions (A-B-A from re-imports) stay three spans, never merged
- An entry without the version key reads `unknown` instead of raising

### test_gap_file_attribution.py

Which collector file holds the data before and after a gap, and how long after the last tick the
next file opened — the evidence for "was the collector running through it". The open time comes from
the file name, written in the collector's own clock, so it goes through the broker's server clock
rule exactly as the importer converts the ticks.

**TestOpenTimeParsing:**
- A Kraken name is already UTC
- An MT5 name in US summer moves back three hours, one in US winter two — a fixed offset gets winter
  wrong
- A name inside the hour a daylight saving change repeats yields nothing rather than a guess, the
  same as a name without a timestamp

**TestAttribution:**
- A gap inside one file, a boundary where the next file opened at the last tick (a rollover), and one
  where it opened deep inside the gap (collection had stopped)
- Segment edges a market-boundary split leaves inside the hole still find the boundary
- Gaps outside the indexed range, no entries, and unsorted entries are handled without a guess

### test_gap_extent_rendering.py

How long a gap was, in the units a reader thinks in: hours always, calendar days once hours stop
being readable, and — only on a market that closes — the trading time actually lost. A 24/7 market is
never asked for trading weeks.

---

## Architecture

- **Pure function, no IO.** The index load lives in the report's render path
  (`DataCoverageReport._version_spans_section`), so the grouping stays testable in isolation and the
  batch validation path never pays for an index it does not read.
- **No quality claim is tested, because none is made.** The version is an operator-set collector
  input describing the declared schema — it does not state how a field was obtained. Deriving
  "authentic vs. reconstructed timing" from it produces false statements on real archives, so the
  spans report structure only.
