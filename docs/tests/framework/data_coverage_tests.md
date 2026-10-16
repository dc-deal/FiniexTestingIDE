# Data Coverage Tests Documentation

## Overview

Covers the pieces of the data coverage report. The **data format version spans**: the tick
index records a `data_format_version` per file, and grouping consecutive files by it turns that
per-file field into archive structure — which collector schema produced which period. The **file
attribution** of a gap: which file holds the data on either side of it, and whose data that is. The
**extent** of a gap as the report renders it. What the report **lists** — the newest gaps of each
category — and how it **reads** a gap by its origin. The **timeline** a run asks the report by,
and the cache **load** every run pays.

**Test Location:** `tests/framework/data_coverage/`
**Units under test:** `python/framework/discoveries/data_coverage/data_format_version_spans.py`,
`gap_file_attribution.py`, `gap_listing.py` and `gap_timeline.py` beside it, the rendering and the
yearly summary of `DataCoverageReport`, the load of `DataCoverageReportCache`, and the kinds of
`GapCategory` (`python/framework/types/coverage_report_types.py`).
The units take index entries or gaps; where a report would read the tick index, the read is
replaced, so no test depends on this machine's archive. Only the cache-load test writes a file, into
the test's own directory.

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

**TestOrigin** (#599):
- Each gap carries the origin class of the files around it; at the seam between the venue's history
  and our first collected file the two differ
- An entry with an unrecognised class reads as `unknown`; a gap outside the indexed range gets none

### test_gap_listing.py

What a report lists (#599): the newest gaps of each category, newest first, the rest counted — the
rule both coverage reports list by.

**TestTheNewestOfACategory:** newest first and no more than the limit · a burst of five hundred short
gaps never pushes out the one large gap · the categories run large, moderate, short, and expected
closures are never listed

**TestTheHeadingSaysHowMuchIsShown:** `the last 10 of 1,034` for a cut list, `all 3` for a whole one

**TestTheSpan:** the end is shortened to what differs from the start — time, date, or year

### test_gap_origin_reading.py

Whose silence a gap is (#599). The gaps carry their files and origin classes already; the tick-index
read is replaced.

**TestWhoseSilenceAGapIs:** a gap in the venue's history reads "no trades at the venue, nothing to
re-collect", inside one file or between two · the seam is named as such · inside our own file the
collector was running · between two of our files, when collection resumed (rollover, hours later,
unknown) · an unattributed gap says so

**TestTheYearlySummaryByOrigin:** counts per year and origin · expected closures are not missing
data · a gap belongs to the year it started and the file it follows · an unattributed gap has a row
of its own, last in its year

**TestRecommendationsAreForOurOwnCollection:** the venue's history alone needs none and the report
says why · a Kraken collection gets the collector advice, never MQL5 · an MT5 collection gets the
MQL5 advice · an unattributed gap is not ours to judge

**TestThePrintedReport:** the newest ten of a long category with its count · one line per gap with
its origin · the yearly summary · no gap in the venue's history reads as a collector fault

### test_gap_timeline.py

The window queries a run asks per scenario (#599) — whether a gap contains the start, which gaps lie
inside the tick stretch — answered by binary search, and held to EXACTLY what the former linear
scans answered. The scans are reproduced in the test as they stood.

**TestTheSearchAnswersWhatTheScanAnswered:** over random archives of non-overlapping gaps, adjacent
split segments included, at every edge, a second either side of it and at random instants — the
containing gap, the gaps inside a window, and a window exactly on a gap's edges. Counter-checked:
a search off by one at the edges disagrees with the scan at 453 probe moments (measured 2026-10-15).

**TestTheEdges:** a moment on an edge is not inside · the seam between two split segments is in
neither · an unsorted list is sorted first · a gap without a time is left out · no gaps answer
nothing

**TestItIsRebuiltWhenTheListChanges:** a timeline covers the list it was built over, not a grown or
a replaced one — a report fills its list after construction

### test_coverage_cache_load.py

The column-wise read of a cached report (#599) — 480 ms for BTCUSD's 117,757 gaps where the
row-by-row read took 3.4 s — hands back exactly what was written: every gap's length, category,
reason and times, the times as UTC `datetime`, and a report without gaps as one without gaps rather
than as an unreadable file that would be regenerated on every run.

### test_gap_extent_rendering.py

How long a gap was, in the units a reader thinks in: hours always, calendar days once hours stop
being readable, and — only on a market that closes — the trading time actually lost. A 24/7 market is
never asked for trading weeks.

### test_coverage_report_text.py

A symbol with no bars — a mistyped one, or bars never rendered — gets a report that says so,
`(no bar data for this symbol)`, rather than a traceback from a time range that does not exist
(#586). That report is what `discoveries_cli.py data-coverage show` prints.

### test_gap_category_kinds.py

**TestEveryGapCategoryIsOneKind:** each gap category is exactly one kind — a data gap (the open
market with no tick), a market closure, or no gap at all (`seamless`). The kind decides what a check
may do with a gap: a start inside an admitted data gap moves to its end, a start inside a closure is
refused. A new category without a kind fails here instead of being treated as neither.
- Every member answers to exactly one kind
- Short, moderate and large are data gaps
- Weekend and holiday are market closures

---

## Architecture

- **Pure function, no IO.** The index load lives in the report's render path
  (`DataCoverageReport._tick_entries`, read once per report), so the grouping stays testable in
  isolation and the batch validation path never pays for an index it does not read.
- **A search must answer what the scan answered.** The timeline replaced two linear scans on the
  run's validation path; its tests reproduce the scans and compare, rather than asserting what the
  search is believed to return.
- **No quality claim is tested, because none is made.** The version is an operator-set collector
  input describing the declared schema — it does not state how a field was obtained. Deriving
  "authentic vs. reconstructed timing" from it produces false statements on real archives, so the
  spans report structure only.
