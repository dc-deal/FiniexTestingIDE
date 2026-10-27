"""
FiniexTestingIDE - Spread Estimate Cache

A backtest over ticks without a quote needs the spread of every day it covers, and measuring it
reads the symbol's whole history without a quote — 17.6 s for kraken_spot ETHUSD (measured
2026-10-15). So the day estimates are a discovery cache family: one file per symbol, one row per
UTC day, measured from the files that recorded no quote and from nothing else.

Valid while two things hold: the files without a quote are the set the cache was built from —
their names, tick counts and time ranges as the tick index lists them, so the check opens no tick
file — and the estimator is the version that wrote it. A new file without a quote, a re-import that
changed one, or a changed method makes it invalid; an import of quoted data does not touch it.

Cache Structure:
    discovery_caches/spread_estimate_cache/
        kraken_spot_ETHUSD.parquet
"""

import hashlib
import json
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
import pyarrow as pa

from python.configuration.app_config_manager import AppConfigManager
from python.data_management.index.tick_index_manager import TickIndexManager
from python.framework.discoveries.discovery_cache_index import DiscoveryCacheIndex
from python.framework.discoveries.spread_estimate.spread_estimate_measurement import (
    SpreadEstimateMeasurement,
)
from python.framework.discoveries.spread_estimate.spread_estimator import ESTIMATOR_VERSION
from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.logging.bootstrap_logger import get_global_logger
from python.framework.types.data_deficiency_types import DataDeficiency
from python.framework.types.spread_estimate_types import SpreadDayEstimate
from python.framework.types.store_types import DISCOVERY_CACHE_DIRNAME
from python.framework.utils.config_fingerprint_utils import (
    generate_config_fingerprint,
    read_cache_metadata,
)
from python.framework.utils.file_utils import create_directory

vLog = get_global_logger()

_COLUMNS = ['day', 'pairs', 'spread_median_bps', 'spread_p90_bps', 'spread_mean_bps']


class SpreadEstimateCache:
    """
    The day estimates of a symbol's spread, cached per symbol and rebuilt when its source changes.

    Args:
        logger: Logger for the build lines
        tick_index: The tick index to read the files without a quote from; loaded on first use
            when not given
        cache_dir: Where the family's files live; the discovery-cache directory when not given
    """

    CACHE_PARENT_DIR = DISCOVERY_CACHE_DIRNAME
    CACHE_SUB_DIR = 'spread_estimate_cache'

    def __init__(self, logger: AbstractLogger = vLog,
                 tick_index: Optional[TickIndexManager] = None,
                 cache_dir: Optional[Path] = None):
        self._logger = logger
        self._tick_index = tick_index
        if cache_dir is None:
            data_dir = Path(AppConfigManager().get_data_processed_path())
            cache_dir = data_dir / self.CACHE_PARENT_DIR / self.CACHE_SUB_DIR
        self.cache_dir = Path(cache_dir)
        create_directory(self.cache_dir, holds='the spread estimate cache')
        # The method's identity; the source's identity is computed per symbol
        self._method_fingerprint = generate_config_fingerprint(
            {'estimator_version': ESTIMATOR_VERSION})

    def get_day_estimates(self, broker_type: str, symbol: str,
                          force_rebuild: bool = False) -> List[SpreadDayEstimate]:
        """
        The symbol's day estimates, from the cache where it is valid and measured where not.

        Args:
            broker_type: The broker
            symbol: The symbol
            force_rebuild: Measure again even when the cache is valid

        Returns:
            One estimate per UTC day with at least one pair, oldest first; empty for a symbol
            whose every file recorded a quote — nothing is written for it
        """
        entries = self._no_quote_entries(broker_type, symbol)
        if not entries:
            return []
        source_fingerprint = _source_fingerprint(entries)
        cache_path = self._get_cache_path(broker_type, symbol)
        if not force_rebuild and self._is_valid(cache_path, source_fingerprint):
            return _load(cache_path)

        measurement = SpreadEstimateMeasurement(self._get_tick_index()).measure_entries(
            broker_type, symbol, entries)
        self._save(cache_path, broker_type, symbol, measurement.day_estimates,
                   source_fingerprint, len(entries))
        return measurement.day_estimates

    def is_cache_valid(self, broker_type: str, symbol: str) -> bool:
        """
        Whether the symbol's cache was built from its current files without a quote, by this
        estimator.

        Args:
            broker_type: The broker
            symbol: The symbol

        Returns:
            True when valid; False when absent, out of date, or when the symbol has no file
            without a quote
        """
        entries = self._no_quote_entries(broker_type, symbol)
        if not entries:
            return False
        return self._is_valid(self._get_cache_path(broker_type, symbol),
                              _source_fingerprint(entries))

    def build_all(self, force_rebuild: bool = False) -> Dict[str, int]:
        """
        Build the cache of every symbol that has a file without a quote.

        Args:
            force_rebuild: Measure again even where the cache is valid

        Returns:
            {generated, skipped, failed}
        """
        stats = {'generated': 0, 'skipped': 0, 'failed': 0}
        start_time = time.time()
        for broker_type, symbol in self._symbols_without_quote():
            try:
                if not force_rebuild and self.is_cache_valid(broker_type, symbol):
                    stats['skipped'] += 1
                    continue
                self.get_day_estimates(broker_type, symbol, force_rebuild=True)
                stats['generated'] += 1
            except Exception as e:
                self._logger.warning(
                    f'Failed to build spread estimate cache for {broker_type}/{symbol}: {e}')
                stats['failed'] += 1
        self._logger.info(
            f"✅ Spread estimate cache built: {stats['generated']} generated, "
            f"{stats['skipped']} skipped, {stats['failed']} failed "
            f'in {time.time() - start_time:.2f}s')
        return stats

    def get_cache_status(self) -> Dict:
        """
        Cache status overview.

        Returns:
            Dict with cache statistics
        """
        cached = stale = missing = 0
        symbols = self._symbols_without_quote()
        for broker_type, symbol in symbols:
            if not self._get_cache_path(broker_type, symbol).exists():
                missing += 1
            elif self.is_cache_valid(broker_type, symbol):
                cached += 1
            else:
                stale += 1
        cache_files = list(self.cache_dir.glob('*.parquet'))
        return {
            'total_symbols': len(symbols),
            'cached_symbols': cached,
            'stale_symbols': stale,
            'missing_symbols': missing,
            'cache_files': len(cache_files),
            'total_cache_size_mb': round(sum(f.stat().st_size for f in cache_files)
                                         / (1024 * 1024), 2),
            'cache_dir': str(self.cache_dir),
        }

    def clear_cache(self) -> int:
        """
        Delete every cached file of the family.

        Returns:
            Number of files deleted
        """
        cache_files = list(self.cache_dir.glob('*.parquet'))
        for cache_file in cache_files:
            cache_file.unlink()
        self._logger.info(f'🗑️ Cleared {len(cache_files)} spread estimate cache files')
        return len(cache_files)

    def _get_tick_index(self) -> TickIndexManager:
        """
        The tick index, loaded once.

        Returns:
            The loaded index
        """
        if self._tick_index is None:
            self._tick_index = TickIndexManager(logger=self._logger)
            self._tick_index.load_index()
        return self._tick_index

    def _no_quote_entries(self, broker_type: str, symbol: str) -> List[Dict]:
        """
        The symbol's tick index entries that recorded no quote.

        Args:
            broker_type: The broker
            symbol: The symbol

        Returns:
            The entries, in index order
        """
        return [entry for entry in self._get_tick_index().get_symbol_entries(broker_type, symbol)
                if DataDeficiency.NO_QUOTE in entry.get('deficiencies', [])]

    def _symbols_without_quote(self) -> List[Tuple[str, str]]:
        """
        Every (broker, symbol) with at least one file without a quote.

        Returns:
            The pairs, sorted
        """
        index = self._get_tick_index()
        return [(broker_type, symbol)
                for broker_type in index.list_broker_types()
                for symbol in index.list_symbols(broker_type)
                if self._no_quote_entries(broker_type, symbol)]

    def _get_cache_path(self, broker_type: str, symbol: str) -> Path:
        """
        The cache file of a symbol.

        Args:
            broker_type: The broker
            symbol: The symbol

        Returns:
            Its path
        """
        return self.cache_dir / f'{broker_type}_{symbol}.parquet'

    def _is_valid(self, cache_path: Path, source_fingerprint: str) -> bool:
        """
        Whether a cache file was written from this source by this estimator.

        Args:
            cache_path: The cache file
            source_fingerprint: The fingerprint of the current files without a quote

        Returns:
            True when both match
        """
        metadata = read_cache_metadata(cache_path)
        if metadata is None:
            return False
        return (metadata.get('config_fingerprint') == self._method_fingerprint
                and metadata.get('source_fingerprint') == source_fingerprint)

    def _save(self, cache_path: Path, broker_type: str, symbol: str,
              estimates: List[SpreadDayEstimate], source_fingerprint: str,
              file_count: int) -> None:
        """
        Write a symbol's day estimates, atomically, and enter the file in the discovery index.

        Atomic because a backtest reads this file to price its fills: a half-written one must
        never be read as a short history.

        Args:
            cache_path: The cache file
            broker_type: The broker
            symbol: The symbol
            estimates: The day estimates
            source_fingerprint: The fingerprint of the files they were measured from
            file_count: How many files without a quote they were measured from
        """
        frame = pd.DataFrame(
            [[estimate.day, estimate.pairs, estimate.spread_median_bps, estimate.spread_p90_bps,
              estimate.spread_mean_bps] for estimate in estimates],
            columns=_COLUMNS)
        schema = pa.schema([('day', pa.date32()), ('pairs', pa.int64()),
                            ('spread_median_bps', pa.float64()), ('spread_p90_bps', pa.float64()),
                            ('spread_mean_bps', pa.float64())])
        table = pa.Table.from_pandas(frame, schema=schema, preserve_index=False)
        table = table.replace_schema_metadata({
            b'config_fingerprint': self._method_fingerprint.encode(),
            b'source_fingerprint': source_fingerprint.encode(),
            b'estimator_version': str(ESTIMATOR_VERSION).encode(),
            b'no_quote_files': str(file_count).encode(),
            b'broker_type': broker_type.encode(),
            b'symbol': symbol.encode(),
            b'generated_at': datetime.now(timezone.utc).isoformat().encode(),
        })
        DiscoveryCacheIndex(self.cache_dir.parent).write_cache_file(table, cache_path)
        self._logger.debug(f'💾 Cached spread estimate: {broker_type}/{symbol}')


def _source_fingerprint(entries: List[Dict]) -> str:
    """
    The identity of a set of tick files, from what the tick index says about each.

    Name, tick count, time range and when the importer wrote the file. The last one is what sees
    a re-import that changed only values: a file re-imported with exact prices keeps its name,
    its count and its range. The check opens no tick file — on the bridged disk one open costs
    milliseconds.

    Args:
        entries: Tick index entries

    Returns:
        A SHA256 hex digest
    """
    described = sorted((str(entry.get('file')), int(entry.get('tick_count') or 0),
                        str(entry.get('start_time')), str(entry.get('end_time')),
                        str(entry.get('processed_at') or ''))
                       for entry in entries)
    return hashlib.sha256(json.dumps(described).encode()).hexdigest()


def _load(cache_path: Path) -> List[SpreadDayEstimate]:
    """
    A cache file's day estimates.

    Args:
        cache_path: The cache file

    Returns:
        One estimate per row, in file order
    """
    frame = pd.read_parquet(cache_path)
    return [SpreadDayEstimate(day=day if isinstance(day, date) else pd.Timestamp(day).date(),
                              pairs=int(pairs), spread_median_bps=float(median),
                              spread_p90_bps=float(p90), spread_mean_bps=float(mean))
            for day, pairs, median, p90, mean in zip(
                frame['day'], frame['pairs'], frame['spread_median_bps'],
                frame['spread_p90_bps'], frame['spread_mean_bps'])]
