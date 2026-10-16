# Spread Estimate Tests Documentation

## Overview

A tick without a quote carries bid = ask = the traded price, so a backtest over it never crosses a
spread. The spread is estimated from the trades instead: every trade records its aggressor side,
and a buy and a sell close together in time straddle the spread. This suite holds the estimator's
pure functions to that reading, and the measurement over an archive to what its files contain.

**Test Location:** `tests/framework/spread_estimate/`
**Units under test:** `spread_estimator.py` and `spread_estimate_measurement.py` in
`python/framework/discoveries/spread_estimate/`.
The measurement tests write small tick files into the test's own directory and pass their index
entries in directly, so no test reads this machine's archive.

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
