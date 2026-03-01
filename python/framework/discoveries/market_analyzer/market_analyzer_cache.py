"""
Market Analyzer Cache
=====================
Parquet-based caching for MarketAnalyzer results (SymbolAnalysis).

Follows DiscoveryCache / DataCoverageReportCache pattern:
- Parquet storage with Arrow metadata
- Invalidation based on source bar file modification time
- Lazy bar index loading

Cache Structure:
    .discovery_caches/market_analyzer_cache/
        mt5_EURUSD_analysis.parquet
        mt5_USDJPY_analysis.parquet
        kraken_spot_BTCUSD_analysis.parquet

Serialization:
    - PeriodAnalysis list -> Parquet rows (enums as strings)
    - Scalar fields, regime dicts, session summaries -> Arrow metadata (JSON)
"""

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from python.configuration.app_config_manager import AppConfigManager
from python.data_management.index.bars_index_manager import BarsIndexManager
from python.framework.discoveries.market_analyzer.market_analyzer import MarketAnalyzer
from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.logging.bootstrap_logger import get_global_logger
from python.framework.types.market_config_types import MarketType
from python.framework.types.scenario_generator_types import (
    PeriodAnalysis,
    SessionSummary,
    SymbolAnalysis,
    TradingSession,
    VolatilityRegime,
)

vLog = get_global_logger()

# Parquet column schema for PeriodAnalysis rows
_PERIOD_COLUMNS = [
    'start_time', 'end_time', 'session', 'atr', 'atr_percentile', 'regime',
    'tick_count', 'tick_density', 'activity', 'bar_count', 'real_bar_count',
    'synthetic_bar_count', 'high', 'low', 'range_pips',
]


class MarketAnalyzerCache:
    """
    Parquet-based cache for MarketAnalyzer SymbolAnalysis results.

    Auto-invalidates when source bar files change (mtime comparison).
    Only caches default M5 timeframe; custom timeframes bypass cache.
    """

    CACHE_PARENT_DIR = ".discovery_caches"
    CACHE_SUB_DIR = "market_analyzer_cache"
    GRANULARITY = "M5"

    def __init__(self, logger: AbstractLogger = vLog):
        """
        Initialize cache.

        Args:
            logger: Logger instance (falls back to global logger)
        """
        self._logger = logger
        self._app_config = AppConfigManager()
        self.data_dir = Path(self._app_config.get_data_processed_path())
        self.cache_dir = self.data_dir / self.CACHE_PARENT_DIR / self.CACHE_SUB_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._bar_index: Optional[BarsIndexManager] = None

    def _get_bar_index(self) -> BarsIndexManager:
        """Lazy-load bar index."""
        if self._bar_index is None:
            self._bar_index = BarsIndexManager(logger=self._logger)
            self._bar_index.build_index()
        return self._bar_index

    def _get_cache_path(self, broker_type: str, symbol: str) -> Path:
        """Get cache file path for broker_type/symbol."""
        return self.cache_dir / f"{broker_type}_{symbol}_analysis.parquet"

    def _get_source_bar_mtime(self, broker_type: str, symbol: str) -> Optional[float]:
        """Get modification time of source M5 bar file."""
        bar_index = self._get_bar_index()
        bar_file = bar_index.get_bar_file(
            broker_type, symbol, self.GRANULARITY)
        if bar_file and bar_file.exists():
            return bar_file.stat().st_mtime
        return None

    # =========================================================================
    # CACHE VALIDITY
    # =========================================================================

    def is_cache_valid(self, broker_type: str, symbol: str) -> bool:
        """
        Check if cache is valid (exists and not stale).

        Cache is invalid if:
        - Cache file doesn't exist
        - Source bar file is newer than cached mtime
        - Source bar file doesn't exist
        """
        cache_path = self._get_cache_path(broker_type, symbol)
        if not cache_path.exists():
            return False

        try:
            pq_file = pq.ParquetFile(cache_path)
            metadata = pq_file.schema_arrow.metadata or {}
            cached_mtime = float(metadata.get(
                b'source_bar_mtime', b'0').decode())
        except Exception:
            return False

        current_mtime = self._get_source_bar_mtime(broker_type, symbol)
        if current_mtime is None:
            return False

        return current_mtime <= cached_mtime

    # =========================================================================
    # MAIN ENTRY POINT
    # =========================================================================

    def get_analysis(
        self,
        broker_type: str,
        symbol: str,
        timeframe: Optional[str] = None,
        force_rebuild: bool = False,
        analyzer: Optional[MarketAnalyzer] = None
    ) -> Optional[SymbolAnalysis]:
        """
        Get SymbolAnalysis, using cache if valid.

        Custom timeframes bypass cache (only M5 is cached).

        Args:
            broker_type: Broker type identifier
            symbol: Trading symbol
            timeframe: Timeframe override (non-M5 bypasses cache)
            force_rebuild: Force reanalysis, ignore cache
            analyzer: Optional pre-initialized MarketAnalyzer (for build_all)

        Returns:
            SymbolAnalysis or None if data unavailable
        """
        # Custom timeframe bypasses cache
        if timeframe and timeframe != self.GRANULARITY:
            self._logger.debug(
                f"Custom timeframe {timeframe}, bypassing cache: "
                f"{broker_type}/{symbol}")
            return self._run_analysis(
                broker_type, symbol, timeframe, analyzer)

        # Check cache
        if not force_rebuild and self.is_cache_valid(broker_type, symbol):
            result = self._load_analysis(broker_type, symbol)
            if result:
                self._logger.debug(
                    f"Cache hit: {broker_type}/{symbol} analysis")
                return result

        # Generate fresh analysis
        self._logger.debug(f"Analyzing: {broker_type}/{symbol}")
        result = self._run_analysis(
            broker_type, symbol, timeframe, analyzer)
        if result:
            self._save_analysis(broker_type, symbol, result)
        return result

    def _run_analysis(
        self,
        broker_type: str,
        symbol: str,
        timeframe: Optional[str],
        analyzer: Optional[MarketAnalyzer]
    ) -> Optional[SymbolAnalysis]:
        """
        Run MarketAnalyzer.analyze_symbol with error handling.

        Args:
            broker_type: Broker type identifier
            symbol: Trading symbol
            timeframe: Timeframe override
            analyzer: Optional pre-initialized MarketAnalyzer

        Returns:
            SymbolAnalysis or None on failure
        """
        try:
            if analyzer is None:
                analyzer = MarketAnalyzer()
            return analyzer.analyze_symbol(broker_type, symbol, timeframe)
        except Exception as e:
            self._logger.warning(
                f"Analysis failed for {broker_type}/{symbol}: {e}")
            return None

    # =========================================================================
    # SERIALIZATION
    # =========================================================================

    def _save_analysis(
        self,
        broker_type: str,
        symbol: str,
        analysis: SymbolAnalysis
    ) -> None:
        """Save SymbolAnalysis to Parquet cache."""
        cache_path = self._get_cache_path(broker_type, symbol)

        try:
            # Convert PeriodAnalysis list to DataFrame rows
            rows = []
            for p in analysis.periods:
                rows.append({
                    'start_time': p.start_time,
                    'end_time': p.end_time,
                    'session': p.session.value,
                    'atr': float(p.atr),
                    'atr_percentile': float(p.atr_percentile),
                    'regime': p.regime.value,
                    'tick_count': int(p.tick_count),
                    'tick_density': float(p.tick_density),
                    'activity': float(p.activity),
                    'bar_count': int(p.bar_count),
                    'real_bar_count': int(p.real_bar_count),
                    'synthetic_bar_count': int(p.synthetic_bar_count),
                    'high': float(p.high),
                    'low': float(p.low),
                    'range_pips': float(p.range_pips),
                })

            if rows:
                df = pd.DataFrame(rows)
            else:
                df = pd.DataFrame(columns=_PERIOD_COLUMNS)

            source_mtime = self._get_source_bar_mtime(
                broker_type, symbol) or 0.0

            # Scalar fields + dicts -> Arrow metadata
            metadata = {
                b'broker_type': broker_type.encode(),
                b'symbol': analysis.symbol.encode(),
                b'timeframe': analysis.timeframe.encode(),
                b'market_type': analysis.market_type.value.encode(),
                b'data_source': analysis.data_source.encode(),
                b'start_time': analysis.start_time.isoformat().encode(),
                b'end_time': analysis.end_time.isoformat().encode(),
                b'total_days': str(analysis.total_days).encode(),
                b'total_bars': str(analysis.total_bars).encode(),
                b'total_ticks': str(analysis.total_ticks).encode(),
                b'real_bar_ratio': str(float(analysis.real_bar_ratio)).encode(),
                b'atr_min': str(float(analysis.atr_min)).encode(),
                b'atr_max': str(float(analysis.atr_max)).encode(),
                b'atr_avg': str(float(analysis.atr_avg)).encode(),
                b'atr_std': str(float(analysis.atr_std)).encode(),
                b'atr_percent': str(float(analysis.atr_percent)).encode(),
                b'total_activity': str(float(analysis.total_activity)).encode(),
                b'avg_pips_per_day': str(
                    float(analysis.avg_pips_per_day)
                    if analysis.avg_pips_per_day is not None else ''
                ).encode(),
                b'regime_distribution': json.dumps(
                    {k.value: v for k, v in analysis.regime_distribution.items()}
                ).encode(),
                b'regime_percentages': json.dumps(
                    {k.value: v for k, v in analysis.regime_percentages.items()}
                ).encode(),
                b'session_summaries': json.dumps(
                    _serialize_session_summaries(analysis.session_summaries)
                ).encode(),
                b'source_bar_mtime': str(source_mtime).encode(),
                b'generated_at': datetime.now(timezone.utc).isoformat().encode(),
            }

            table = pa.Table.from_pandas(df)
            table = table.replace_schema_metadata({
                **(table.schema.metadata or {}),
                **metadata
            })

            pq.write_table(table, cache_path)
            self._logger.debug(f"Cached: {broker_type}/{symbol} analysis")

        except Exception as e:
            self._logger.warning(
                f"Failed to cache {broker_type}/{symbol} analysis: {e}")

    def _load_analysis(
        self,
        broker_type: str,
        symbol: str
    ) -> Optional[SymbolAnalysis]:
        """Load SymbolAnalysis from Parquet cache."""
        cache_path = self._get_cache_path(broker_type, symbol)

        try:
            pq_file = pq.ParquetFile(cache_path)
            metadata = pq_file.schema_arrow.metadata or {}
            df = pd.read_parquet(cache_path)

            # Reconstruct PeriodAnalysis list from DataFrame rows
            periods: List[PeriodAnalysis] = []
            for _, row in df.iterrows():
                periods.append(PeriodAnalysis(
                    start_time=row['start_time'].to_pydatetime(),
                    end_time=row['end_time'].to_pydatetime(),
                    session=TradingSession(row['session']),
                    atr=float(row['atr']),
                    atr_percentile=float(row['atr_percentile']),
                    regime=VolatilityRegime(row['regime']),
                    tick_count=int(row['tick_count']),
                    tick_density=float(row['tick_density']),
                    activity=float(row['activity']),
                    bar_count=int(row['bar_count']),
                    real_bar_count=int(row['real_bar_count']),
                    synthetic_bar_count=int(row['synthetic_bar_count']),
                    high=float(row['high']),
                    low=float(row['low']),
                    range_pips=float(row['range_pips']),
                ))

            # Reconstruct regime dicts from metadata
            regime_distribution = {
                VolatilityRegime(k): v
                for k, v in json.loads(
                    metadata.get(b'regime_distribution', b'{}').decode()
                ).items()
            }
            regime_percentages = {
                VolatilityRegime(k): v
                for k, v in json.loads(
                    metadata.get(b'regime_percentages', b'{}').decode()
                ).items()
            }

            # Reconstruct session summaries from metadata
            session_summaries = _deserialize_session_summaries(
                json.loads(
                    metadata.get(b'session_summaries', b'{}').decode()
                )
            )

            # Reconstruct avg_pips_per_day (Optional)
            pips_str = metadata.get(b'avg_pips_per_day', b'').decode()
            avg_pips_per_day = float(pips_str) if pips_str else None

            return SymbolAnalysis(
                symbol=metadata.get(b'symbol', b'').decode(),
                timeframe=metadata.get(b'timeframe', b'M5').decode(),
                market_type=MarketType(
                    metadata.get(b'market_type', b'forex').decode()),
                data_source=metadata.get(b'data_source', b'').decode(),
                start_time=datetime.fromisoformat(
                    metadata.get(b'start_time', b'').decode()),
                end_time=datetime.fromisoformat(
                    metadata.get(b'end_time', b'').decode()),
                total_days=int(metadata.get(b'total_days', b'0').decode()),
                total_bars=int(metadata.get(b'total_bars', b'0').decode()),
                total_ticks=int(metadata.get(b'total_ticks', b'0').decode()),
                real_bar_ratio=float(
                    metadata.get(b'real_bar_ratio', b'0').decode()),
                atr_min=float(metadata.get(b'atr_min', b'0').decode()),
                atr_max=float(metadata.get(b'atr_max', b'0').decode()),
                atr_avg=float(metadata.get(b'atr_avg', b'0').decode()),
                atr_std=float(metadata.get(b'atr_std', b'0').decode()),
                atr_percent=float(
                    metadata.get(b'atr_percent', b'0').decode()),
                total_activity=float(
                    metadata.get(b'total_activity', b'0').decode()),
                avg_pips_per_day=avg_pips_per_day,
                regime_distribution=regime_distribution,
                regime_percentages=regime_percentages,
                session_summaries=session_summaries,
                periods=periods,
            )

        except Exception as e:
            self._logger.warning(
                f"Failed to load cache for {broker_type}/{symbol}: {e}")
            return None

    # =========================================================================
    # BULK OPERATIONS
    # =========================================================================

    def build_all(self, force_rebuild: bool = False) -> Dict[str, int]:
        """
        Build cache for all symbols in bar index.

        Args:
            force_rebuild: Force reanalysis of all symbols

        Returns:
            Dict with statistics {generated, skipped, failed}
        """
        bar_index = self._get_bar_index()
        stats = {'generated': 0, 'skipped': 0, 'failed': 0}
        start_time = time.time()

        # Single analyzer instance for all symbols (expensive init)
        analyzer = MarketAnalyzer()

        for broker_type in bar_index.list_broker_types():
            for symbol in bar_index.list_symbols(broker_type):
                try:
                    if not force_rebuild and self.is_cache_valid(
                        broker_type, symbol
                    ):
                        stats['skipped'] += 1
                        continue

                    result = self.get_analysis(
                        broker_type, symbol,
                        force_rebuild=True, analyzer=analyzer)
                    if result:
                        stats['generated'] += 1
                    else:
                        stats['failed'] += 1

                except Exception as e:
                    self._logger.warning(
                        f"Failed to build cache for "
                        f"{broker_type}/{symbol}: {e}")
                    stats['failed'] += 1

        elapsed = time.time() - start_time
        total = stats['generated'] + stats['skipped'] + stats['failed']
        self._logger.info(
            f"Market analyzer cache built: {stats['generated']} generated, "
            f"{stats['skipped']} skipped, {stats['failed']} failed "
            f"({total} total) in {elapsed:.2f}s"
        )

        return stats

    def clear_cache(self) -> int:
        """
        Clear all cached analysis results.

        Returns:
            Number of files deleted
        """
        cache_files = list(self.cache_dir.glob("*.parquet"))
        for cache_file in cache_files:
            cache_file.unlink()
        self._logger.info(
            f"Cleared {len(cache_files)} market analyzer cache files")
        return len(cache_files)

    def get_cache_status(self) -> Dict:
        """
        Get cache status overview.

        Returns:
            Dict with cache statistics
        """
        bar_index = self._get_bar_index()

        total_symbols = 0
        cached = 0
        stale = 0
        missing = 0

        cache_files = list(self.cache_dir.glob("*.parquet"))
        total_size_mb = sum(
            f.stat().st_size for f in cache_files) / (1024 * 1024)

        for broker_type in bar_index.list_broker_types():
            for symbol in bar_index.list_symbols(broker_type):
                total_symbols += 1
                cache_path = self._get_cache_path(broker_type, symbol)
                if not cache_path.exists():
                    missing += 1
                elif self.is_cache_valid(broker_type, symbol):
                    cached += 1
                else:
                    stale += 1

        return {
            'total_symbols': total_symbols,
            'cached': cached,
            'stale': stale,
            'missing': missing,
            'cache_files': len(cache_files),
            'total_size_mb': round(total_size_mb, 2),
            'cache_dir': str(self.cache_dir),
        }


# =============================================================================
# SESSION SUMMARY SERIALIZATION HELPERS
# =============================================================================

def _serialize_session_summaries(
    summaries: Dict[TradingSession, SessionSummary]
) -> Dict[str, Dict]:
    """
    Serialize SessionSummary dict to JSON-compatible structure.

    Args:
        summaries: Dict mapping TradingSession to SessionSummary

    Returns:
        Dict with string keys and plain-dict values
    """
    result: Dict[str, Dict] = {}
    for session, summary in summaries.items():
        result[session.value] = {
            'period_count': summary.period_count,
            'avg_atr': float(summary.avg_atr),
            'min_atr': float(summary.min_atr),
            'max_atr': float(summary.max_atr),
            'total_ticks': int(summary.total_ticks),
            'avg_tick_density': float(summary.avg_tick_density),
            'min_tick_density': float(summary.min_tick_density),
            'max_tick_density': float(summary.max_tick_density),
            'total_activity': float(summary.total_activity),
            'regime_distribution': {
                k.value: v
                for k, v in summary.regime_distribution.items()
            },
        }
    return result


def _deserialize_session_summaries(
    data: Dict[str, Dict]
) -> Dict[TradingSession, SessionSummary]:
    """
    Deserialize JSON dict back to SessionSummary instances.

    Args:
        data: Dict with string keys from JSON

    Returns:
        Dict mapping TradingSession to SessionSummary
    """
    result: Dict[TradingSession, SessionSummary] = {}
    for session_str, values in data.items():
        session = TradingSession(session_str)
        regime_dist = {
            VolatilityRegime(k): v
            for k, v in values.get('regime_distribution', {}).items()
        }
        result[session] = SessionSummary(
            session=session,
            period_count=int(values['period_count']),
            avg_atr=float(values['avg_atr']),
            min_atr=float(values['min_atr']),
            max_atr=float(values['max_atr']),
            total_ticks=int(values['total_ticks']),
            avg_tick_density=float(values['avg_tick_density']),
            min_tick_density=float(values['min_tick_density']),
            max_tick_density=float(values['max_tick_density']),
            total_activity=float(values['total_activity']),
            regime_distribution=regime_dist,
        )
    return result
