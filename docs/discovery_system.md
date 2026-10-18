# Discovery System

The discovery system provides pre-computed market analyses with automatic cache invalidation. All discoveries run through `discoveries_cli.py` and share a unified cache infrastructure.

## Components

| Component | Purpose | Output |
|-----------|---------|--------|
| **Volatility Profile Analyzer** | ATR volatility, session activity, regime classification | `SymbolVolatilityProfile` |
| **Extreme Move Scanner** | Directional price movements (strong LONG/SHORT trends) | `ExtremeMoveResult` |
| **Data Coverage** | Gap detection, data quality assessment | `DataCoverageReport` |
| **Signal Coverage** | Gap detection on a signal series (#429) | `SignalCoverageReport` |
| **Spread Estimate** | The spread of days whose ticks recorded no quote, from the trades' aggressor sides (#602) | `SpreadMeasurement`, cached day estimates |

## Architecture

```
discoveries_cli.py
    ├── volatility-profile → VolatilityProfileAnalyzerCache → VolatilityProfileAnalyzer
    ├── extreme-moves      → DiscoveryCache                 → ExtremeMoveScanner
    ├── data-coverage      → DataCoverageReportCache        → DataCoverageReport
    ├── signal-coverage    → SignalCoverageReportManager    → SignalCoverageReport
    └── cache              → DiscoveryCacheManager            (coordinates the cached families)

spread_estimate_cli.py
    ├── show               → SpreadEstimateMeasurement       (the whole history, measured)
    └── build              → SpreadEstimateCache             (the day estimates a backtest reads)
```

**Code locations:**
- Volatility profiling: `python/framework/discoveries/volatility_profile_analyzer/`
- Data coverage: `python/framework/discoveries/data_coverage/`
- Signal coverage: `python/framework/discoveries/signal_coverage/`
- Spread estimate: `python/framework/discoveries/spread_estimate/`, types in `spread_estimate_types.py`
- Types: `python/framework/types/market_types/market_volatility_profile_types.py`, `coverage_report_types.py`
- Config: `configs/discoveries/discoveries_config.json` (volatility_profile, cross_instrument_ranking, extreme_moves, data_coverage, signal_coverage)
- CLI: `python/cli/discoveries_cli.py`

## Cache System

All caches follow the same pattern:
- **Storage**: Parquet files with Arrow metadata
- **Invalidation**: against the source each family reads, plus a fingerprint of what produced it.
  The three bar-derived families compare the M5 bar file's mtime (stale if the source is newer)
  and their config section. The spread estimate compares the set of the symbol's files without a
  quote — names, tick counts and time ranges as the tick index lists them — and the estimator's
  version, so neither a quoted import nor a config change elsewhere touches it, while a changed
  method does
- **Lazy loading**: the bar or tick index is only loaded on the first cache check
- **Bulk operations**: `build_all()`, `clear_cache()`, `get_cache_status()`
- **The store index keeps up**: every cache write enters its file in
  `discovery_caches_index.parquet`, so a report computed on demand does not leave the store catalog
  reporting a stale index. One row costs about 45 ms, a full index rebuild about 850 ms

### Directory Structure

```
data/processed/discovery_caches/
├── data_coverage_cache/            # DataCoverageReportCache
│   └── {broker}_{symbol}.parquet
├── extreme_moves_cache/            # DiscoveryCache
│   └── {broker}_{symbol}_extreme_moves.parquet
├── volatility_profile_cache/       # VolatilityProfileAnalyzerCache
│   └── {broker}_{symbol}_volatility_profile.parquet
└── spread_estimate_cache/          # SpreadEstimateCache — only symbols with a file without a quote
    └── {broker}_{symbol}.parquet
```

### DiscoveryCacheManager

Central coordinator (`discovery_cache_manager.py`). Used by:
- `bar_importer.py` — auto-rebuild after bar import
- `discoveries_cli.py` — `cache rebuild-all`, `cache status`

Methods: `rebuild_all(force)`, `status()`, `get_fingerprints(broker, symbol)`

`force` reaches the bar-derived families only. The spread estimate's validity is exact — the files
without a quote and the estimator version — so a forced rebuild would write the same rows, and
re-measuring the whole history after every import that forces the others would cost minutes
(about two for every Kraken symbol, measured 2026-10-16). `spread_estimate_cli.py build --force`
forces it on purpose.

### Serialization

| Cache | Parquet Rows | Arrow Metadata |
|-------|-------------|----------------|
| **Volatility Profile** | `VolatilityPeriod` list (enums as strings) | Scalars, regime dicts, session summaries (JSON) |
| **Extreme Moves** | `ExtremeMove` list (direction as string) | Timeframe, ATR, pip_size, scanned_bars |
| **Data Coverage** | `Gap` list (category as string) | Start/end time, gap_counts (JSON) |
| **Spread Estimate** | One row per UTC day: pairs, median, p90 and capped mean in bps | Estimator-version fingerprint, source fingerprint, number of files without a quote |

## CLI Reference

```
discoveries_cli.py volatility-profile <broker> <symbol> [--timeframe M5] [--force]
discoveries_cli.py extreme-moves <broker> <symbol> [--top 10] [--force]
discoveries_cli.py data-coverage show <broker> <symbol> [--force]
discoveries_cli.py data-coverage validate
discoveries_cli.py data-coverage status
discoveries_cli.py data-coverage build [--force]
discoveries_cli.py data-coverage clear
discoveries_cli.py signal-coverage show <data_sentiment_type> <symbol>
discoveries_cli.py signal-coverage validate
discoveries_cli.py cache rebuild-all [--force]
discoveries_cli.py cache status
spread_estimate_cli.py show <broker> <symbol> [--year 2021]
spread_estimate_cli.py build [<broker> <symbol>] [--force]
```

**`--force`** bypasses cache and recomputes from source data.

## Volatility Profile Analyzer Details

Analyzes M5 bar data per symbol:
- Groups bars into 1-hour periods
- Classifies volatility regime (VERY_LOW to VERY_HIGH) relative to average ATR
- Aggregates by trading session (Sydney/Tokyo, London, New York, Transition)
- Computes cross-instrument ranking (ATR%, liquidity, combined score)

Output: `SymbolVolatilityProfile` dataclass with `periods`, `session_summaries`, `regime_distribution`.

**Session bucketing**: All markets — including 24/7 crypto — are bucketed into the same four
time-of-day windows (Sydney/Tokyo, London, New York, Transition). For forex this maps directly to
exchange sessions. For crypto, the same bucketing is valid because institutional participants,
CME/CBOE futures arbitrage, and US macro news flow create activity patterns that closely follow
traditional finance schedules. Empirical data confirms this: BTCUSD on Kraken shows ~1.6× higher
volume during the New York window compared to the Asian window. Industry platforms (Bloomberg
Terminal, TradingView, Kaiko) use the same Asia/Europe/US bucketing for crypto analytics. Markets
without native sessions display the section header as "TIME-OF-DAY ACTIVITY" instead of "SESSION
ACTIVITY" (controlled by `session_bucketing` in market config).

**Cache behavior**: Only M5 timeframe is cached. Custom `--timeframe` values bypass cache and compute directly.

## Extreme Move Scanner Details

Scans bar data with sliding windows to find extreme directional price movements:
- ATR-normalized scoring (move_atr_multiple)
- Separate LONG/SHORT rankings
- Configurable via `configs/discoveries/discoveries_config.json` (sections `volatility_profile`, `extreme_moves`)

### Data Coverage Awareness

After deduplication, discovered moves are filtered against the Data Coverage report. Moves whose
`start_time` falls within a **weekend**, **holiday**, or **large** data gap are removed. This
prevents selecting time windows where tick data is absent or unreliable (e.g. a Sunday start where
no real ticks are available for backtesting).

The filter uses `DataCoverageReportCache` to load the gap report for the broker/symbol pair. If no coverage report exists, filtering is skipped with a warning.

## Data Coverage Details

Detects gaps via timestamp jumps between consecutive bars at the configured granularity (default: M1):
- **Weekend**: Expected closure (Friday 17:00 - Sunday 17:00 New York: 21:00 UTC in summer, 22:00 in winter)
- **Holiday**: Dec 25, Jan 1
- **Short**: < 30min
- **Moderate**: 30min - 4h
- **Large**: > 4h

Provides `has_issues()` and `get_recommendations()`. The recommendations are for the gaps of our
OWN collection only (see *Whose silence a gap is* below), and the MT5 advice — the MQL5
TickCollector's logs — is given for an MT5 broker only; a Kraken collection gets a general hint at
the collector's logs. A gap's `reason` names the category and the length and gives no advice: the
classifier cannot know whose data surrounds a gap.

### What the report lists

A venue's published history holds tens of thousands of gaps — `kraken_spot/BTCUSD` 117,757, LTCUSD
375,682, against about 400 for an MT5 symbol with one year of our own collection (measured
2026-10-15) — and a report that printed every moderate and large one ran to 61,178 lines. The report
lists the **newest ten of each category**, newest first, one line each, and counts the rest by year
and data origin:

```
🔴 LARGE — the last 10 of 1,034
   2026-09-20 19:48 → 09-21 08:39  12h 51m (12.85h)  between two files, collection resumed 12h 50m later   [production]
   2025-11-01 15:02 → 21:46        6h 44m (6.73h)    inside BTCUSD_20251101_000000.parquet — no trades at the venue, nothing to re-collect   [external]
⚠️  MODERATE — the last 10 of 7,694
ℹ️  SHORT — the last 10 of 109,029

BY YEAR AND ORIGIN (short · moderate · large, and the hours without data):
   year  origin            short  moderate   large       hours
   2016  external         48,138     1,422       8     6,908.9
   2026  production          365         3       2        46.7
```

Per category, so a burst of recent short gaps never pushes out the one large gap. The rule lives in
`gap_listing.py` and the signal coverage report lists by it too. The yearly summary is computed on
the report (`summary_by_year_and_origin()`), not in the printout, and the API serves the same rows.
Weekend and holiday closures are not counted in it — they are a market's expected silence. A gap
belongs to the year it started in.

### Whose silence a gap is

Every tick file carries its **origin class** (`production`, `development`, `unknown`, `external`),
and a gap takes the class of the file it lies in or follows. In a venue's published history
(`external`) no collector of ours was running: a gap there is a stretch in which nobody traded at
the venue, and there is nothing to re-collect — the line says so and the recommendations leave it
out. Where the class before and after a gap differ, the gap is the **seam** between two origins: the
line reads "where the external data ends and the production data begins", and the gaps route serves
two different classes in `origin_class` and `origin_class_after`. A gap no tick file could be found
for is `unattributed`, and a report holding one closes with "Not judged" rather than a clean bill.

A gap starts at the open of the last bar before it, and that bar's ticks run on to its end, so the
file before a gap is the last one that opened before that bar closed — a file whose first tick falls
inside the bar holds the data before the gap.

### What a run asks of the report, and what it costs

Every batch loads each symbol's report from the cache once and asks it, per scenario, whether a gap
contains the scenario's start and which gaps lie inside its tick stretch. Both questions are a
binary search over the gaps' start times (`gap_timeline.py`), because a linear scan grew with the
archive's depth: 166 ms per scenario on BTCUSD's 117,757 gaps against 0.7 ms on an MT5 symbol
(measured 2026-10-15). The search is built once per symbol and batch — 29 ms for BTCUSD, 102 ms for
LTCUSD's 375,682 gaps. The cache is read column by column for the same reason — 480 ms for BTCUSD
where the row-by-row read took 3.4 s. Nothing the report adds for a reader (origin, listing,
summary) runs on that path.

Categories are gated on the market type. `MarketCalendar` is forex-shaped — its weekend and
trading-hour methods hard-code Mon–Fri — so a 24/7 market never receives the weekend/holiday
categories, and its moderate gaps are not split into "during" / "outside" trading hours. On a
market that never closes, every hour is a trading hour and a Saturday gap is a real gap.

### How long a gap was

Hours always; calendar days from a full day up, where hours stop being readable. On a market
that closes, a second line states the **trading time actually lost** — the figure that matters
for an archive, because a weekend costs no trading day while a fortnight costs ten:

```
Gap:    118h (118.00h · 4.9d)
Lost:   5 trading days (1 full trading week)
```

The `Lost:` line appears only for markets with a weekend closure. A 24/7 market has no trading
week, so calendar days are already the whole truth there — the market-type gate lives in the caller,
because `MarketCalendar.get_trading_days()` counts Mon–Fri without knowing the market type. It
also appears only from a full day up: the count is calendar-day based and would read a two-hour
Wednesday gap as "1 trading day".

### Which file(s) a gap falls in

Each listed gap states where it sits relative to the collector's files. In our own collection this
answers the operator question behind every outage: **was the collector running through it, or not
collecting at all?**

```
inside ADAUSD_20260728_233104.parquet, the collector was running
between two files, at a rollover — collection continued
between two files, collection resumed 336h later
```

**A file boundary on its own proves nothing.** Files roll at `max_ticks_per_file`, not on a clock,
so a boundary can fall inside a running session purely by coincidence — measured across the archive,
97 of 178 boundary gaps were such rollovers. The implication only runs one way: a collector that was
down always produces a boundary, but a boundary does not imply a collector that was down.

What separates them is the **file's open time**, taken from the file name and converted through the
same server clock rule the importer uses (MT5 names carry broker server time, Kraken names carry UTC).
A file that opened at the previous file's last tick rolled over; one that opened inside the gap marks
the moment collection resumed. The metadata's `start_time_unix` is not an alternative — it is the
server wall-clock read as if it were UTC, so for MT5 it is off by the server's offset.

The report states the observation and stops there. Whether a pause was the venue or the operator is
a judgment, and it stays with the reader.

Resolved **at render time, from the tick index** — like the version spans below, and for the same
reason: the batch validation path never renders the report and must not pay for the index load. The
origin class is read from the same entries in the same pass, and the index is read once per report —
loaded, never built: an index that cannot be read leaves the gaps unattributed rather than a report,
or an API request, rebuilding it.

### Data format version spans

The report shows **which collector schema produced which window** of the archive. The tick index
records a `data_format_version` per file; consecutive files sharing a version collapse into one span:

```
DATA FORMAT VERSION:
   1.0.5   2026-01-24 14:22 → 2026-01-25 14:13     1 file,          2,560 ticks
   1.2.0   2026-01-25 14:15 → 2026-03-07 08:15     8 files,       330,679 ticks
   1.3.0   2026-03-07 08:18 → 2026-08-15 15:16    31 files,     1,387,190 ticks
```

**The version is a declaration, not a measurement.** It comes from an operator-set collector input
(`DataFormatVersion` in the MQL5 collector), so it states which schema the collector was configured
to announce — never how a field was obtained. A collector upgrade that starts recording a field
without a version bump is invisible here, and that has happened: the MT5 archive kept declaring
`1.1.0` across the point where its `collected_msc` became a real collector timestamp. Do not derive
data quality from these spans; they describe archive structure.

Spans are built **at render time, from the tick index — never cached**. The coverage cache keys its
validity on the bar file's mtime, while the tick index changes independently of that, so a cached
span list could go stale unnoticed. The batch validation path never renders the report and therefore
never pays for the index load.

## Signal Coverage Details

The signal-source sibling of Data Coverage, keyed by `(data_sentiment_type, symbol)`.
Detects gaps via timestamp jumps between consecutive **snapshots** (the signal series is
the event sequence itself — there are no bars to walk).

Three deliberate differences to the tick report:

| | Data Coverage | Signal Coverage |
|---|---|---|
| Key | `(broker_type, symbol)` | `(data_sentiment_type, symbol)` |
| Interval | configured granularity (M1) | **measured** median snapshot distance |
| Weekend | expected closure (per market rules) | **always a real gap** — the producing engine runs 24/7 |
| Provenance | version spans only — a *declared* schema, no origin claim | `data_origin` per source (live / synthetic / mixed), **measured** from the archive |

- **Thresholds** (`signal_coverage.thresholds`): short < 30min, moderate < 1h, large above.
  Tighter than the tick ladder — no producer restart takes longer than an hour.
- **Coverage is envelope-level.** An envelope carrying no result for the symbol
  (partial/error) is a *degraded* snapshot, not a gap — that distinction belongs to the
  runtime resolution (`basis` / `status` / `is_stale`), not the timeline.
- **No cache.** A report reads two projected columns from a handful of parquet files;
  a cache would be dead weight.

### Data origin — the mock-versus-real discriminator

The report also reads the `data_origin` column: `synthetic` (generated), `live`
(producer output), `mixed` (a source carrying both), or empty. **Empty means unknown,
never an assertion of realness** — archives produced before the field existed carry no
column at all, and the reader projects it only where the schema has it.

```
Origin:       🧪 SYNTHETIC — generated data, not a market record
Config:       #mock-1e9e9fc4
Triggers:     1,008 scheduled · 83 breaking
```

Alongside it the report reads `config_fingerprint` (the producer's input-config hash — `mixed`
flags a config change *inside* the archive) and `trigger_reason` (why each pass ran, counted per
envelope). The trigger composition replaces a timing heuristic: telling a grid pass from an
off-grid one used to mean "distance to predecessor < 300s", which misclassifies whenever a
scheduled pass runs long. Where a producer gained the field mid-archive, the unattributed
envelopes are stated rather than dropped, so the composition always sums to the snapshot count:
`54 scheduled · 2 boot · 2 breaking · 2,454 unknown (pre-contract)`. Field-level detail:
[Signal Data Source](data_pipeline/signal_data_source.md).

The purpose is a guard, not decoration. Without it, a generated archive and a real one
are indistinguishable in every field — same `pipeline_id`, same `prompt_hash` — so a
backtest spanning both produces something that looks like a result. A scenario binding a
`synthetic` source therefore gets a pre-run warning (see the table below); it still runs,
it just says what it is.

### Scenario validation

The reports feed `ScenarioDataValidator` through the batch's Phase 1 → 2 → 5 path, keyed
alongside the tick reports. **Phase 2** runs pre-load, **Phase 5** post-load against the
actually-loaded tick stretch:

| Case | Phase | Outcome |
|---|---|---|
| scenario binds no signal source | — | skipped |
| source declares `data_origin: synthetic` | 2 | **warning** `data_synthetic` — envelopes the mock producer generated (the `*_mock` sources), not a market record (the deficiency `synthetic`) |
| source/symbol not imported | 2 | **error** — scenario excluded, batch continues |
| window closes before the series opens | 2 | **error** — no snapshot can ever resolve |
| no snapshot at or before `start_date` | 2 | **warning** — run starts blind, blind duration named |
| `start_date` inside a gap | 2 | **warning** — age of the snapshot the run starts on |
| window reaches past the last snapshot | — | **no finding** — contracted degradation (#434) |
| forbidden gap inside the tick stretch | 5 | **error** — category not in `allowed_gap_categories` |
| gap outside the tick stretch | 5 | ignored — a signal is only resolved at ticks |

Allowed categories come from the **existing** `data_validation.allowed_gap_categories`
(`app_config.json`) — one setting across both data planes, no second knob.

Example — a scenario left on an old window after its source was re-imported:

```
❌ EURUSD_sentiment_demo: Signal 'forex_macro_sentiment': the scenario window
   (2026-05-03 23:00 → 2026-05-04 02:00 UTC) closes before the source begins
   (2026-07-22 09:37 UTC). No snapshot can ever resolve.
```

…and one that merely opens early, which still runs:

```
⚠️  BTCUSD_run: Signal 'crypto_sentiment': no snapshot at or before start_date
   2026-07-22 07:00:00 UTC — the first 2h 37m resolve BLIND (empty signal,
   is_stale) — counted as blind ticks in the run's signal report.
   First snapshot: 2026-07-22 09:37:33 UTC
```

There is **no warmup concept for signals** — a SIGNAL worker resolves the nearest snapshot
at or before the tick and needs no history window. The precondition the Phase 2 check
enforces is the signal analogue: *a snapshot must already exist at the first tick*.

## Spread Estimate Details

A tick recorded without a quote — Kraken's published history, and our own collection before
format 1.6.0 — carries bid = ask = the traded price, so a backtest over it would cross no spread.
Kraken marks the aggressor of every trade, so the spread is estimated from the trades: two
consecutive trades of opposite sides within one second straddle it, a burst of fills of one order
counted once. Per UTC day the cache keeps the pairs, the median, the 90th percentile and the mean
capped at the 99th percentile — the mean is the expected cost, the median is not one: in 2017 more
than half of ETHUSD's pairs crossed at an unchanged price. On the files that did record a quote the
estimate lies between the quoted and the effective spread (`spread_estimate_cli.py show`).

**How a backtest uses it.** While the ticks are prepared for a scenario, each tick from a file
without a quote is given bid and ask half the estimate below and above its traded price, times
`trade_simulator_config.modelled_spread_factor` (see
[Configuration Cascade](config_cascade_guide.md)). From there every rule that reads a quote reads
it unchanged: a market order, a triggered stop and a close take its side, a resting order is reached
by it, an open position is marked at it — in both account models. The archive, the bars and the
traded price stay as recorded.

- **A day is priced by the days before it, never by itself:** the last day with at least 20 pairs,
  at most 30 days back. A tick with no such day keeps bid = ask and is counted as not modelled.
- **What it was given is recorded.** Each fill says whether its quote was modelled, and its spread
  is `modelled_spread_cost` rather than `spread_cost`; the run's scenario details carry the
  factor, the estimator version and a digest of the day estimates used; one run line counts the
  modelled ticks.
- **If the estimates cannot be built**, the scenario is refused (`modelled_spread_unavailable`)
  rather than run without a spread.
- **Not modelled:** mock AutoTrader sessions, which rehearse the live path whose venue sends a
  quote, and every tick that recorded one.

## VS Code Launch Configs

All discovery entries carry the `🔍 Disc -` prefix; they sit in the `📚 COVERAGE & GAPS`,
`🔄 REBUILDS` and `🔍 DISCOVERY ANALYSIS` groups. For example:

```
🔍 Disc - Cache: Rebuild All
🔍 Disc - Cache: Status
🔍 Disc - Volatility Profile: mt5/USDJPY
🔍 Disc - Volatility Profile: kraken_spot/BTCUSD
🔍 Disc - Extreme Moves: mt5/USDJPY
🔍 Disc - Extreme Moves: kraken_spot/BTCUSD
🔍 Disc - Data Coverage: Status
🔍 Disc - Data Coverage: Validate All
🔍 Disc - Data Coverage: mt5/EURUSD
🔍 Disc - Data Coverage: mt5/USDJPY
🔍 Disc - Data Coverage: kraken_spot/BTCUSD
```
