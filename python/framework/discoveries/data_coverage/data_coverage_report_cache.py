"""
DataCoverageReportCache - Parquet-based caching for Coverage Reports
================================================================

Caches gap analysis results to avoid expensive bar-file scanning.
Invalidation based on source bar file modification time.

Architecture:
- DataCoverageReport remains UNCHANGED
- Cache wraps and hydrates DataCoverageReport instances
- Storage: .discovery_caches/data_coverage_cache/{broker_type}_{symbol}.parquet
"""

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from python.configuration.app_config_manager import AppConfigManager
from python.configuration.discoveries_config_loader import DiscoveriesConfigLoader
from python.data_management.index.bars_index_manager import BarsIndexManager
from python.framework.utils.config_fingerprint_utils import generate_config_fingerprint, read_fingerprint_from_parquet
from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.logging.bootstrap_logger import get_global_logger
from python.framework.discoveries.data_coverage.data_coverage_report import DataCoverageReport
from python.framework.types.trading_env_types.broker_types import BrokerType
from python.framework.types.coverage_report_types import Gap, GapCategory

vLog = get_global_logger()


class DataCoverageReportCache:
    """
    Parquet-based cache for DataCoverageReport gap analysis.

    Avoids expensive bar-file scanning by caching gap results.
    Auto-invalidates when source bar files change.

    Cache Structure:
        .discovery_caches/data_coverage_cache/
            mt5_EURUSD.parquet
            mt5_USDJPY.parquet
            kraken_spot_BTCUSD.parquet

    Each parquet file contains:
        - Gap data (gap_start, gap_end, gap_seconds, category, reason)
        - Metadata (start_time, end_time, gap_counts, source_bar_mtime)
    """

    CACHE_PARENT_DIR = ".discovery_caches"
    CACHE_SUB_DIR = "data_coverage_cache"

    def __init__(self, logger: AbstractLogger = vLog):
        self.logger = logger
        self._app_config = AppConfigManager()
        self.data_dir = Path(self._app_config.get_data_processed_path())
        self.cache_dir = self.data_dir / self.CACHE_PARENT_DIR / self.CACHE_SUB_DIR

        # Read granularity from discoveries config (consistent with DataCoverageReport)
        discoveries_config = DiscoveriesConfigLoader().get_config_raw()
        gap_config = discoveries_config.get('data_coverage', {})
        self._granularity = gap_config.get('granularity', 'M5')

        # Ensure cache directory exists
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # Bar index for source file lookup
        self._bar_index: Optional[BarsIndexManager] = None

    def _get_bar_index(self) -> BarsIndexManager:
        """Lazy-load bar index."""
        if self._bar_index is None:
            self._bar_index = BarsIndexManager(logger=self.logger)
            self._bar_index.build_index()
        return self._bar_index

    def _get_cache_path(self, broker_type: str, symbol: str) -> Path:
        """Get cache file path for broker_type/symbol."""
        return self.cache_dir / f"{broker_type}_{symbol}.parquet"

    def _get_source_bar_mtime(self, broker_type: str, symbol: str) -> Optional[float]:
        """Get modification time of source bar file at configured granularity."""
        bar_index = self._get_bar_index()
        bar_file = bar_index.get_bar_file(
            broker_type, symbol, self._granularity)

        if bar_file and bar_file.exists():
            return bar_file.stat().st_mtime
        return None

    def _get_current_config_fingerprint(self) -> str:
        """Get SHA256 fingerprint of current data_coverage config section."""
        config = DiscoveriesConfigLoader().get_config_raw()
        section = config.get('data_coverage', {})
        return generate_config_fingerprint(section)

    def get_config_fingerprint(self, broker_type: str, symbol: str) -> Optional[str]:
        """
        Get stored config fingerprint from cached coverage report.

        Args:
            broker_type: Broker type identifier
            symbol: Trading symbol

        Returns:
            Fingerprint string or None if not cached
        """
        cache_path = self._get_cache_path(broker_type, symbol)
        return read_fingerprint_from_parquet(cache_path)

    def is_cache_valid(self, broker_type: str, symbol: str) -> bool:
        """
        Check if cache is valid (exists and not stale).

        Cache is invalid if:
        - Cache file doesn't exist
        - Source bar file is newer than cache
        - Source bar file doesn't exist
        - Granularity has changed since cache was built
        """
        cache_path = self._get_cache_path(broker_type, symbol)

        if not cache_path.exists():
            return False

        # Get source bar mtime and granularity from cache metadata
        try:
            pq_file = pq.ParquetFile(cache_path)
            metadata = pq_file.schema_arrow.metadata or {}
            cached_mtime = float(metadata.get(
                b'source_bar_mtime', b'0').decode())
            cached_granularity = metadata.get(
                b'granularity', b'').decode()
        except Exception:
            return False

        # Invalidate if granularity changed
        if cached_granularity != self._granularity:
            return False

        # Compare with current bar file mtime
        current_mtime = self._get_source_bar_mtime(broker_type, symbol)

        if current_mtime is None:
            return False

        return current_mtime <= cached_mtime

    def get_report(
        self,
        broker_type: str,
        symbol: str,
        force_rebuild: bool = False
    ) -> Optional[DataCoverageReport]:
        """
        Get DataCoverageReport, using cache if valid.

        Args:
            broker_type: Broker type identifier
            symbol: Trading symbol
            force_rebuild: Force regeneration, ignore cache

        Returns:
            DataCoverageReport instance or None if data unavailable
        """
        # Convert string to BrokerType if needed
        if isinstance(broker_type, str):
            broker_type_enum = BrokerType(broker_type)
        else:
            broker_type_enum = broker_type
            broker_type = broker_type.value

        # Check cache
        if not force_rebuild and self.is_cache_valid(broker_type, symbol):
            report = self._load_from_cache(
                broker_type, symbol, broker_type_enum)
            if report:
                self.logger.debug(f"📦 Cache hit: {broker_type}/{symbol}")
                return report

        # Generate fresh report
        self.logger.debug(f"🔄 Generating report: {broker_type}/{symbol}")
        report = DataCoverageReport(symbol=symbol, broker_type=broker_type)
        report.analyze()

        # Cache the result
        self._save_to_cache(broker_type, symbol, report)

        return report

    def _load_from_cache(
        self,
        broker_type: str,
        symbol: str,
        broker_type_enum: BrokerType
    ) -> Optional[DataCoverageReport]:
        """Load DataCoverageReport from cache and hydrate."""
        cache_path = self._get_cache_path(broker_type, symbol)

        try:
            # Read parquet with metadata
            pq_file = pq.ParquetFile(cache_path)
            metadata = pq_file.schema_arrow.metadata or {}
            df = pd.read_parquet(cache_path)

            # Create empty report
            report = DataCoverageReport(
                symbol=symbol, broker_type=broker_type)

            # Hydrate from metadata
            report.start_time = pd.to_datetime(
                metadata.get(b'start_time', b'').decode()
            ).to_pydatetime() if metadata.get(b'start_time') else None

            report.end_time = pd.to_datetime(
                metadata.get(b'end_time', b'').decode()
            ).to_pydatetime() if metadata.get(b'end_time') else None

            report.gap_counts = json.loads(
                metadata.get(b'gap_counts', b'{}').decode()
            )

            # Hydrate gaps from DataFrame
            report.gaps = []
            for _, row in df.iterrows():
                gap = Gap(
                    gap_seconds=float(row['gap_seconds']),
                    category=GapCategory(row['category']),
                    reason=row['reason'],
                    gap_start=row['gap_start'].to_pydatetime(
                    ) if pd.notna(row['gap_start']) else None,
                    gap_end=row['gap_end'].to_pydatetime() if pd.notna(
                        row['gap_end']) else None,
                )
                report.gaps.append(gap)

            return report

        except Exception as e:
            self.logger.warning(
                f"Failed to load cache for {broker_type}/{symbol}: {e}")
            return None

    def _save_to_cache(
        self,
        broker_type: str,
        symbol: str,
        report: DataCoverageReport
    ) -> None:
        """Save DataCoverageReport to cache."""
        cache_path = self._get_cache_path(broker_type, symbol)

        try:
            # Convert gaps to DataFrame
            rows = []
            for gap in report.gaps:
                rows.append({
                    'gap_start': gap.gap_start,
                    'gap_end': gap.gap_end,
                    'gap_seconds': gap.gap_seconds,
                    'category': gap.category.value,
                    'reason': gap.reason,
                })

            if rows:
                df = pd.DataFrame(rows)
            else:
                df = pd.DataFrame(columns=[
                    'gap_start', 'gap_end', 'gap_seconds', 'category', 'reason'
                ])

            # Prepare metadata
            source_mtime = self._get_source_bar_mtime(
                broker_type, symbol) or 0.0

            metadata = {
                b'start_time': report.start_time.isoformat().encode() if report.start_time else b'',
                b'end_time': report.end_time.isoformat().encode() if report.end_time else b'',
                b'gap_counts': json.dumps(report.gap_counts).encode(),
                b'source_bar_mtime': str(source_mtime).encode(),
                b'generated_at': datetime.now(timezone.utc).isoformat().encode(),
                b'config_fingerprint': self._get_current_config_fingerprint().encode(),
                b'broker_type': broker_type.encode(),
                b'symbol': symbol.encode(),
                b'granularity': self._granularity.encode(),
            }

            # Write parquet with metadata
            table = pa.Table.from_pandas(df)
            table = table.replace_schema_metadata({
                **(table.schema.metadata or {}),
                **metadata
            })

            pq.write_table(table, cache_path)
            self.logger.debug(f"💾 Cached: {broker_type}/{symbol}")

        except Exception as e:
            self.logger.warning(f"Failed to cache {broker_type}/{symbol}: {e}")

    def build_all(self, force_rebuild: bool = False) -> Dict[str, int]:
        """
        Build cache for all symbols in bar index.

        Args:
            force_rebuild: Force regeneration of all caches

        Returns:
            Dict with statistics {generated, skipped, failed}
        """
        bar_index = self._get_bar_index()

        stats = {'generated': 0, 'skipped': 0, 'failed': 0}

        start_time = time.time()

        for broker_type in bar_index.list_broker_types():
            for symbol in bar_index.list_symbols(broker_type):
                try:
                    if not force_rebuild and self.is_cache_valid(broker_type, symbol):
                        stats['skipped'] += 1
                        continue

                    report = self.get_report(
                        broker_type, symbol, force_rebuild=True)
                    if report:
                        stats['generated'] += 1
                    else:
                        stats['failed'] += 1

                except Exception as e:
                    self.logger.warning(
                        f"Failed to build cache for {broker_type}/{symbol}: {e}")
                    stats['failed'] += 1

        elapsed = time.time() - start_time
        total = stats['generated'] + stats['skipped'] + stats['failed']

        self.logger.info(
            f"✅ Coverage cache built: {stats['generated']} generated, "
            f"{stats['skipped']} skipped, {stats['failed']} failed "
            f"({total} total) in {elapsed:.2f}s"
        )

        return stats

    def get_cache_status(self) -> Dict:
        """
        Get cache status overview.

        Returns:
            Dict with cache statistics
        """
        bar_index = self._get_bar_index()

        total_symbols = 0
        cached_symbols = 0
        stale_symbols = 0
        missing_symbols = 0

        cache_files = list(self.cache_dir.glob("*.parquet"))
        total_cache_size_mb = sum(
            f.stat().st_size for f in cache_files) / (1024 * 1024)

        for broker_type in bar_index.list_broker_types():
            for symbol in bar_index.list_symbols(broker_type):
                total_symbols += 1
                cache_path = self._get_cache_path(broker_type, symbol)

                if not cache_path.exists():
                    missing_symbols += 1
                elif self.is_cache_valid(broker_type, symbol):
                    cached_symbols += 1
                else:
                    stale_symbols += 1

        return {
            'total_symbols': total_symbols,
            'cached_symbols': cached_symbols,
            'stale_symbols': stale_symbols,
            'missing_symbols': missing_symbols,
            'cache_files': len(cache_files),
            'total_cache_size_mb': round(total_cache_size_mb, 2),
            'cache_dir': str(self.cache_dir),
        }

    def clear_cache(self) -> int:
        """
        Clear all cached reports.

        Returns:
            Number of files deleted
        """
        cache_files = list(self.cache_dir.glob("*.parquet"))

        for cache_file in cache_files:
            cache_file.unlink()

        self.logger.info(f"🗑️ Cleared {len(cache_files)} cache files")
        return len(cache_files)

    def print_status(self) -> None:
        """Print cache status to console."""
        status = self.get_cache_status()

        print("\n" + "="*60)
        print("📦 Coverage Report Cache Status")
        print("="*60)
        print(f"Cache Dir:     {status['cache_dir']}")
        print(f"Cache Files:   {status['cache_files']}")
        print(f"Cache Size:    {status['total_cache_size_mb']:.2f} MB")
        print("-"*60)
        print(f"Total Symbols: {status['total_symbols']}")
        print(f"  ✅ Cached:   {status['cached_symbols']}")
        print(f"  ⚠️  Stale:    {status['stale_symbols']}")
        print(f"  ❌ Missing:  {status['missing_symbols']}")
        print("="*60 + "\n")
