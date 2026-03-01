"""
Discovery Cache
================
Parquet-based caching for discovery results (extreme moves, etc.).

Follows CoverageReportCache pattern:
- Parquet storage with Arrow metadata
- Invalidation based on source bar file modification time
- Lazy bar index loading

Cache Structure:
    .discovery_cache/
        mt5_EURUSD_extreme_moves.parquet
        mt5_USDJPY_extreme_moves.parquet
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
from python.framework.types.discovery_types import (
    ExtremeMove,
    ExtremeMoveResult,
    MoveDirection,
)
from python.framework.discoveries.extreme_move_scanner import ExtremeMoveScanner
from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.logging.bootstrap_logger import get_global_logger

vLog = get_global_logger()


class DiscoveryCache:
    """
    Parquet-based cache for discovery results.

    Auto-invalidates when source bar files change (mtime comparison).
    """

    CACHE_DIR_NAME = ".discovery_cache"
    GRANULARITY = "M5"

    def __init__(self, logger: AbstractLogger = vLog):
        self._logger = logger
        self._app_config = AppConfigManager()
        self.data_dir = Path(self._app_config.get_data_processed_path())
        self.cache_dir = self.data_dir / self.CACHE_DIR_NAME
        self.cache_dir.mkdir(exist_ok=True)
        self._bar_index: Optional[BarsIndexManager] = None

    def _get_bar_index(self) -> BarsIndexManager:
        """Lazy-load bar index."""
        if self._bar_index is None:
            self._bar_index = BarsIndexManager(logger=self._logger)
            self._bar_index.build_index()
        return self._bar_index

    def _get_cache_path(self, broker_type: str, symbol: str, discovery_type: str) -> Path:
        """Get cache file path."""
        return self.cache_dir / f"{broker_type}_{symbol}_{discovery_type}.parquet"

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

    def is_cache_valid(self, broker_type: str, symbol: str, discovery_type: str) -> bool:
        """
        Check if cache is valid (exists and not stale).

        Cache is invalid if:
        - Cache file doesn't exist
        - Source bar file is newer than cached mtime
        - Source bar file doesn't exist
        """
        cache_path = self._get_cache_path(broker_type, symbol, discovery_type)
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
    # EXTREME MOVES
    # =========================================================================

    def get_extreme_moves(
        self,
        broker_type: str,
        symbol: str,
        force_rebuild: bool = False
    ) -> Optional[ExtremeMoveResult]:
        """
        Get extreme move results, using cache if valid.

        Args:
            broker_type: Broker type identifier
            symbol: Trading symbol
            force_rebuild: Force regeneration, ignore cache

        Returns:
            ExtremeMoveResult or None if data unavailable
        """
        discovery_type = "extreme_moves"

        if not force_rebuild and self.is_cache_valid(broker_type, symbol, discovery_type):
            result = self._load_extreme_moves(broker_type, symbol)
            if result:
                self._logger.debug(
                    f"Cache hit: {broker_type}/{symbol} extreme_moves")
                return result

        self._logger.debug(f"Scanning: {broker_type}/{symbol} extreme_moves")
        scanner = ExtremeMoveScanner(logger=self._logger)
        result = scanner.scan(broker_type, symbol)

        self._save_extreme_moves(broker_type, symbol, result)
        return result

    def _load_extreme_moves(
        self,
        broker_type: str,
        symbol: str
    ) -> Optional[ExtremeMoveResult]:
        """Load ExtremeMoveResult from cache."""
        cache_path = self._get_cache_path(broker_type, symbol, "extreme_moves")

        try:
            pq_file = pq.ParquetFile(cache_path)
            metadata = pq_file.schema_arrow.metadata or {}
            df = pd.read_parquet(cache_path)

            timeframe = metadata.get(b'timeframe', b'M5').decode()
            avg_atr = float(metadata.get(b'avg_atr', b'0').decode())
            pip_size = float(metadata.get(b'pip_size', b'0').decode())
            scanned_bars = int(metadata.get(b'scanned_bars', b'0').decode())
            generated_at_str = metadata.get(b'generated_at', b'').decode()
            generated_at = (
                datetime.fromisoformat(generated_at_str)
                if generated_at_str
                else datetime.now(timezone.utc)
            )

            longs: List[ExtremeMove] = []
            shorts: List[ExtremeMove] = []

            for _, row in df.iterrows():
                move = ExtremeMove(
                    broker_type=broker_type,
                    symbol=symbol,
                    timeframe=timeframe,
                    direction=MoveDirection(row['direction']),
                    start_time=row['start_time'].to_pydatetime(),
                    end_time=row['end_time'].to_pydatetime(),
                    bar_count=int(row['bar_count']),
                    entry_price=float(row['entry_price']),
                    extreme_price=float(row['extreme_price']),
                    exit_price=float(row['exit_price']),
                    move_pips=float(row['move_pips']),
                    move_atr_multiple=float(row['move_atr_multiple']),
                    max_adverse_pips=float(row['max_adverse_pips']),
                    window_atr=float(row.get('window_atr', 0.0)),
                    tick_count=int(row['tick_count']),
                )
                if move.direction == MoveDirection.LONG:
                    longs.append(move)
                else:
                    shorts.append(move)

            return ExtremeMoveResult(
                broker_type=broker_type,
                symbol=symbol,
                timeframe=timeframe,
                longs=longs,
                shorts=shorts,
                scanned_bars=scanned_bars,
                avg_atr=avg_atr,
                pip_size=pip_size,
                generated_at=generated_at,
            )

        except Exception as e:
            self._logger.warning(
                f"Failed to load cache for {broker_type}/{symbol}: {e}")
            return None

    def _save_extreme_moves(
        self,
        broker_type: str,
        symbol: str,
        result: ExtremeMoveResult
    ) -> None:
        """Save ExtremeMoveResult to cache."""
        cache_path = self._get_cache_path(broker_type, symbol, "extreme_moves")

        try:
            rows = []
            for move in result.longs + result.shorts:
                rows.append({
                    'direction': move.direction.value,
                    'start_time': move.start_time,
                    'end_time': move.end_time,
                    'bar_count': move.bar_count,
                    'entry_price': move.entry_price,
                    'extreme_price': move.extreme_price,
                    'exit_price': move.exit_price,
                    'move_pips': move.move_pips,
                    'move_atr_multiple': move.move_atr_multiple,
                    'max_adverse_pips': move.max_adverse_pips,
                    'window_atr': move.window_atr,
                    'tick_count': move.tick_count,
                })

            if rows:
                df = pd.DataFrame(rows)
            else:
                df = pd.DataFrame(columns=[
                    'direction', 'start_time', 'end_time', 'bar_count',
                    'entry_price', 'extreme_price', 'exit_price',
                    'move_pips', 'move_atr_multiple', 'max_adverse_pips',
                    'window_atr', 'tick_count',
                ])

            source_mtime = self._get_source_bar_mtime(
                broker_type, symbol) or 0.0

            metadata = {
                b'broker_type': broker_type.encode(),
                b'symbol': symbol.encode(),
                b'timeframe': result.timeframe.encode(),
                b'avg_atr': str(result.avg_atr).encode(),
                b'pip_size': str(result.pip_size).encode(),
                b'scanned_bars': str(result.scanned_bars).encode(),
                b'source_bar_mtime': str(source_mtime).encode(),
                b'generated_at': result.generated_at.isoformat().encode(),
            }

            table = pa.Table.from_pandas(df)
            table = table.replace_schema_metadata({
                **(table.schema.metadata or {}),
                **metadata
            })

            pq.write_table(table, cache_path)
            self._logger.debug(f"Cached: {broker_type}/{symbol} extreme_moves")

        except Exception as e:
            self._logger.warning(
                f"Failed to cache {broker_type}/{symbol}: {e}")

    # =========================================================================
    # BULK OPERATIONS
    # =========================================================================

    def build_all(self, force_rebuild: bool = False) -> Dict[str, int]:
        """
        Build cache for all symbols in bar index.

        Returns:
            Dict with statistics {generated, skipped, failed}
        """
        bar_index = self._get_bar_index()
        stats = {'generated': 0, 'skipped': 0, 'failed': 0}
        start_time = time.time()

        for broker_type in bar_index.list_broker_types():
            for symbol in bar_index.list_symbols(broker_type):
                try:
                    if not force_rebuild and self.is_cache_valid(
                        broker_type, symbol, "extreme_moves"
                    ):
                        stats['skipped'] += 1
                        continue

                    result = self.get_extreme_moves(
                        broker_type, symbol, force_rebuild=True)
                    if result:
                        stats['generated'] += 1
                    else:
                        stats['failed'] += 1

                except Exception as e:
                    self._logger.warning(
                        f"Failed to build cache for {broker_type}/{symbol}: {e}")
                    stats['failed'] += 1

        elapsed = time.time() - start_time
        total = stats['generated'] + stats['skipped'] + stats['failed']
        self._logger.info(
            f"Discovery cache built: {stats['generated']} generated, "
            f"{stats['skipped']} skipped, {stats['failed']} failed "
            f"({total} total) in {elapsed:.2f}s"
        )

        return stats

    def clear_cache(self) -> int:
        """Clear all cached discovery results."""
        cache_files = list(self.cache_dir.glob("*.parquet"))
        for cache_file in cache_files:
            cache_file.unlink()
        self._logger.info(f"Cleared {len(cache_files)} discovery cache files")
        return len(cache_files)

    def get_cache_status(self) -> Dict:
        """Get cache status overview."""
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
                cache_path = self._get_cache_path(
                    broker_type, symbol, "extreme_moves")
                if not cache_path.exists():
                    missing += 1
                elif self.is_cache_valid(broker_type, symbol, "extreme_moves"):
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
