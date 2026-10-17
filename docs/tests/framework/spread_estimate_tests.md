# Spread Estimate Tests Documentation

## Overview

A tick without a quote carries bid = ask = the traded price, so a backtest over it never crosses a
spread. The spread is estimated from the trades instead: every trade records its aggressor side,
and a buy and a sell close together in time straddle the spread. This suite holds the estimator's
pure functions to that reading, and the measurement over an archive to what its files contain.

**Test Location:** `tests/framework/spread_estimate/`
**Units under test:** `spread_estimator.py`, `spread_estimate_measurement.py` and
`spread_estimate_cache.py` in `python/framework/discoveries/spread_estimate/`, and the discovery
index's one-row update the cache writes through.
The measurement and cache tests write small tick files into the test's own directory
(`tests/shared/spread_tick_files.py`) and hand their index entries in directly, so no test reads
this machine's archive or writes its caches.

---

## Test Files

### test_spread_estimator.py

**TestABurstCountsOnce:**
- Fills of one order in one millisecond and on one side collapse to the first
- Opposite sides in one millisecond stay two trades

**TestOppositeSidesStraddleTheSpread:**
- A buy then a sell measures the spread in basis points of their midpoint
- Same-side trades form no pair; trades further apart than the gap form none either
- A single trade gives nothing

**TestRollIsTheReference:**
- Trades at random sides of a fixed quote give the spread — Roll's own assumption
- Strictly alternating sides break that assumption and double the estimate
- A steady trend gives no estimate, and neither do too few prices

**TestTheQuotedTruth:**
- The quoted spread in basis points of the midpoint
- A fill at the ask has an effective spread equal to the quoted one; a fill through it costs more

**Standalone:**
- A UTC day starts at midnight

### test_spread_estimate_measurement.py

**TestTheEstimateFromTrades:**
- Pairs and their spread land under their year, origin class and quote regime; a burst counts once
  and a trade five seconds later pairs with nothing
- A day carries its own estimate, the capped mean among it
- A pair across the boundary of two files is kept, whatever order the entries arrive in

**TestTheQuotedTruthBesideIt:**
- A quoted file reports the quoted and the effective spread, and a validation day
- An unquoted file reports no truth

**Standalone:**
- A file across New Year splits its figures by year

### test_spread_estimate_cache.py

**TestTheCacheHoldsTheDayEstimates:**
- A built cache reads back the day, the pairs and the capped mean that were measured
- A valid cache is read without measuring the history again
- A symbol whose every file recorded a quote caches nothing and writes no file

**TestItsValidityIsTheSourceAndTheMethod:**
- A new file without a quote, or a changed one, makes the cache invalid
- A new quoted file leaves it valid — an import of quoted data does not re-measure the history
- A file re-imported with other values makes it invalid — it keeps its name, count and range,
  and the importer's `processed_at` stamp is what tells
- A new estimator version makes it invalid

**TestBuildingEverySymbol:**
- Only symbols with a file without a quote are built; a second run skips them as valid

**TestTheDiscoveryIndexKeepsUp:**
- A cache written on demand is entered in the discovery index, which then reports nothing stale
- A rewritten file keeps one row
- An index already behind for other files stays reported as behind
