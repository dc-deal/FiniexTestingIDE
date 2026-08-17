# Discovery System

The discovery system provides pre-computed market analyses with automatic cache invalidation. All discoveries run through `discoveries_cli.py` and share a unified cache infrastructure.

## Components

| Component | Purpose | Output |
|-----------|---------|--------|
| **Volatility Profile Analyzer** | ATR volatility, session activity, regime classification | `SymbolVolatilityProfile` |
| **Extreme Move Scanner** | Directional price movements (strong LONG/SHORT trends) | `ExtremeMoveResult` |
| **Data Coverage** | Gap detection, data quality assessment | `DataCoverageReport` |
| **Signal Coverage** | Gap detection on a signal series (#429) | `SignalCoverageReport` |

## Architecture

```
discoveries_cli.py
    ├── volatility-profile → VolatilityProfileAnalyzerCache → VolatilityProfileAnalyzer
    ├── extreme-moves      → DiscoveryCache                 → ExtremeMoveScanner
    ├── data-coverage      → DataCoverageReportCache        → DataCoverageReport
    ├── signal-coverage    → SignalCoverageReportManager    → SignalCoverageReport
    └── cache              → DiscoveryCacheManager            (coordinates the cached three)
```

**Code locations:**
- Volatility profiling: `python/framework/discoveries/volatility_profile_analyzer/`
- Data coverage: `python/framework/discoveries/data_coverage/`
- Signal coverage: `python/framework/discoveries/signal_coverage/`
- Types: `python/framework/types/market_types/market_volatility_profile_types.py`, `coverage_report_types.py`
- Config: `configs/discoveries/discoveries_config.json` (volatility_profile, cross_instrument_ranking, extreme_moves, data_coverage, signal_coverage)
- CLI: `python/cli/discoveries_cli.py`

## Cache System

All caches follow the same pattern:
- **Storage**: Parquet files with Arrow metadata
- **Invalidation**: Source M5 bar file mtime comparison (stale if source newer than cache)
- **Lazy loading**: Bar index only loaded on first cache check
- **Bulk operations**: `build_all()`, `clear_cache()`, `get_cache_status()`

### Directory Structure

```
data/processed/.discovery_caches/
├── data_coverage_cache/            # DataCoverageReportCache
│   └── {broker}_{symbol}.parquet
├── extreme_moves_cache/            # DiscoveryCache
│   └── {broker}_{symbol}_extreme_moves.parquet
└── volatility_profile_cache/       # VolatilityProfileAnalyzerCache
    └── {broker}_{symbol}_volatility_profile.parquet
```

### DiscoveryCacheManager

Central coordinator (`discovery_cache_manager.py`). Used by:
- `bar_importer.py` — auto-rebuild after bar import
- `discoveries_cli.py` — `cache rebuild-all`, `cache status`

Methods: `rebuild_all(force)`, `status()`, `clear_all()`

### Serialization

| Cache | Parquet Rows | Arrow Metadata |
|-------|-------------|----------------|
| **Volatility Profile** | `VolatilityPeriod` list (enums as strings) | Scalars, regime dicts, session summaries (JSON) |
| **Extreme Moves** | `ExtremeMove` list (direction as string) | Timeframe, ATR, pip_size, scanned_bars |
| **Data Coverage** | `Gap` list (category as string) | Start/end time, gap_counts (JSON) |

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
```

**`--force`** bypasses cache and recomputes from source data.

## Volatility Profile Analyzer Details

Analyzes M5 bar data per symbol:
- Groups bars into 1-hour periods
- Classifies volatility regime (VERY_LOW to VERY_HIGH) relative to average ATR
- Aggregates by trading session (Sydney/Tokyo, London, New York, Transition)
- Computes cross-instrument ranking (ATR%, liquidity, combined score)

Output: `SymbolVolatilityProfile` dataclass with `periods`, `session_summaries`, `regime_distribution`.

**Session bucketing**: All markets — including 24/7 crypto — are bucketed into the same four time-of-day windows (Sydney/Tokyo, London, New York, Transition). For forex this maps directly to exchange sessions. For crypto, the same bucketing is valid because institutional participants, CME/CBOE futures arbitrage, and US macro news flow create activity patterns that closely follow traditional finance schedules. Empirical data confirms this: BTCUSD on Kraken shows ~1.6× higher volume during the New York window compared to the Asian window. Industry platforms (Bloomberg Terminal, TradingView, Kaiko) use the same Asia/Europe/US bucketing for crypto analytics. Markets without native sessions display the section header as "TIME-OF-DAY ACTIVITY" instead of "SESSION ACTIVITY" (controlled by `session_bucketing` in market config).

**Cache behavior**: Only M5 timeframe is cached. Custom `--timeframe` values bypass cache and compute directly.

## Extreme Move Scanner Details

Scans bar data with sliding windows to find extreme directional price movements:
- ATR-normalized scoring (move_atr_multiple)
- Separate LONG/SHORT rankings
- Configurable via `configs/discoveries/discoveries_config.json` (sections `volatility_profile`, `extreme_moves`)

### Data Coverage Awareness

After deduplication, discovered moves are filtered against the Data Coverage report. Moves whose `start_time` falls within a **weekend**, **holiday**, or **large** data gap are removed. This prevents selecting time windows where tick data is absent or unreliable (e.g. a Sunday start where no real ticks are available for backtesting).

The filter uses `DataCoverageReportCache` to load the gap report for the broker/symbol pair. If no coverage report exists, filtering is skipped with a warning.

## Data Coverage Details

Detects gaps via timestamp jumps between consecutive bars at the configured granularity (default: M1):
- **Weekend**: Expected closure (Fri 21:00 - Sun 21:00 UTC)
- **Holiday**: Dec 25, Jan 1
- **Short**: < 30min (MT5 restarts)
- **Moderate**: 30min - 4h
- **Large**: > 4h (data collection issue)

Provides `has_issues()` check and actionable `get_recommendations()`.

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
| source declares `data_origin: synthetic` | 2 | **warning** — generated data, not a market record |
| source/symbol not imported | 2 | **error** — scenario excluded, batch continues (§33) |
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

## VS Code Launch Configs

All discovery entries are grouped under the `DISCOVERIES` section with `🔍 Disc -` prefix:

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
