# Discovery System

The discovery system provides pre-computed market analyses with automatic cache invalidation. All discoveries run through `discoveries_cli.py` and share a unified cache infrastructure.

## Components

| Component | Purpose | Output |
|-----------|---------|--------|
| **Volatility Profile Analyzer** | ATR volatility, session activity, regime classification | `SymbolVolatilityProfile` |
| **Extreme Move Scanner** | Directional price movements (strong LONG/SHORT trends) | `ExtremeMoveResult` |
| **Data Coverage** | Gap detection, data quality assessment | `DataCoverageReport` |

## Architecture

```
discoveries_cli.py
    ├── volatility-profile → VolatilityProfileAnalyzerCache → VolatilityProfileAnalyzer
    ├── extreme-moves      → DiscoveryCache                 → ExtremeMoveScanner
    ├── data-coverage      → DataCoverageReportCache        → DataCoverageReport
    └── cache              → DiscoveryCacheManager            (coordinates all three)
```

**Code locations:**
- Volatility profiling: `python/framework/discoveries/volatility_profile_analyzer/`
- Data coverage: `python/framework/discoveries/data_coverage/`
- Types: `python/framework/types/market_types/market_volatility_profile_types.py`, `coverage_report_types.py`
- Config: `configs/discoveries/discoveries_config.json` (volatility_profile, cross_instrument_ranking, extreme_moves, data_coverage)
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
