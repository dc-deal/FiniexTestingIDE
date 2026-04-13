"""
Bar Importer - Pre-Render Bars from Tick Data
==============================================

Orchestrates bar pre-rendering for all symbols and timeframes.
Called automatically after tick import or manually for re-rendering.

Supports parallel rendering via ProcessPoolExecutor (symbol-level granularity).
All broker_types and symbols are rendered in one pool.

Workflow:
1. Load ALL tick files for a symbol
2. Call VectorizedBarRenderer for all timeframes
3. Write bar parquet files (one per timeframe)
4. Update bar index (caller responsibility)

"""

from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import List, Optional, Tuple
import time
import traceback

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from python.configuration.import_config_manager import ImportConfigManager
from python.framework.data_preparation.tick_parquet_reader import read_tick_parquet
from python.configuration.market_config_manager import MarketConfigManager
from python.data_management.importers.vectorized_bar_renderer import VectorizedBarRenderer
from python.data_management.index.tick_index_manager import TickIndexManager
from python.data_management.index.bars_index_manager import BarsIndexManager


from python.framework.logging.bootstrap_logger import get_global_logger
from python.framework.discoveries.discovery_cache_manager import DiscoveryCacheManager
from python.framework.types.import_result_types import BarRenderResult
vLog = get_global_logger()


# =============================================================================
# TOP-LEVEL WORKER FUNCTION (required for multiprocessing pickle)
# =============================================================================

def _render_symbol_worker(
    symbol: str,
    broker_type: str,
    data_dir: str,
    importer_version: str
) -> BarRenderResult:
    """
    Standalone worker function for parallel bar rendering.

    Runs in a subprocess — must be top-level for pickle compatibility.
    Each worker builds its own TickIndexManager and VectorizedBarRenderer.
    All log output is buffered and returned in the result.

    Args:
        symbol: Trading symbol to render
        broker_type: Broker type identifier
        data_dir: Path to data directory (string for pickle)
        importer_version: BarImporter.VERSION string

    Returns:
        BarRenderResult with rendered bar count, log buffer, and status
    """
    log_buffer: list[str] = []
    data_path = Path(data_dir)

    try:
        start_time = time.time()

        # === 1. BUILD OWN TICK INDEX ===
        tick_index = TickIndexManager(data_dir=data_dir)
        tick_index.build_index()

        # === 2. LOAD TICK DATA ===
        log_buffer.append(f"  ├─ Loading tick data for {broker_type}/{symbol}...")
        ticks_df = _load_all_ticks_for_symbol(tick_index, symbol, broker_type, log_buffer)

        if ticks_df.empty:
            log_buffer.append(f"  └─ No tick data found for {broker_type}/{symbol}")
            return BarRenderResult(
                symbol=symbol,
                broker_type=broker_type,
                bars_rendered=0,
                success=True,
                log_buffer=log_buffer
            )

        log_buffer.append(f"  ├─ Loaded {len(ticks_df):,} ticks")

        # === 2.5 EXTRACT SOURCE VERSIONS ===
        tick_files = [
            Path(entry['path'])
            for entry in tick_index.index[broker_type][symbol]
        ]
        source_version_min, source_version_max = _extract_source_versions(
            tick_files, log_buffer)

        # === 3. RENDER BARS ===
        log_buffer.append(f"  ├─ Rendering bars...")
        renderer = VectorizedBarRenderer(symbol, broker_type, log_buffer=log_buffer)
        all_bars = renderer.render_all_timeframes(ticks_df)

        # === 4. WRITE BAR FILES ===
        log_buffer.append(f"  ├─ Writing bar files...")
        bars_written = 0

        for timeframe, bars_df in all_bars.items():
            if len(bars_df) > 0:
                _write_bar_file(
                    data_path, symbol, timeframe, bars_df,
                    broker_type, importer_version,
                    source_version_min, source_version_max,
                    log_buffer
                )
                bars_written += len(bars_df)

        # === 5. LOG STATISTICS ===
        elapsed = time.time() - start_time
        log_buffer.append(
            f"  └─ ✅ {broker_type}/{symbol}: {bars_written:,} bars across "
            f"{len(all_bars)} timeframes in {elapsed:.2f}s"
        )

        return BarRenderResult(
            symbol=symbol,
            broker_type=broker_type,
            bars_rendered=bars_written,
            success=True,
            log_buffer=log_buffer
        )

    except Exception as e:
        log_buffer.append(f"  └─ ❌ ERROR: {str(e)}")
        log_buffer.append(traceback.format_exc())
        return BarRenderResult(
            symbol=symbol,
            broker_type=broker_type,
            bars_rendered=0,
            success=False,
            error_message=f"ERROR in {broker_type}/{symbol}: {str(e)}",
            log_buffer=log_buffer
        )


# =============================================================================
# WORKER HELPER FUNCTIONS (top-level for pickle)
# =============================================================================

def _load_all_ticks_for_symbol(
    tick_index: TickIndexManager,
    symbol: str,
    broker_type: str,
    log_buffer: list[str]
) -> pd.DataFrame:
    """
    Load ALL tick files for a symbol.

    Args:
        tick_index: TickIndexManager instance (worker-local)
        symbol: Trading symbol
        broker_type: Broker type identifier
        log_buffer: Buffer for log messages

    Returns:
        DataFrame with all ticks for symbol
    """
    if broker_type not in tick_index.index:
        log_buffer.append(
            f"  ⚠️ Broker type '{broker_type}' not found in tick index")
        return pd.DataFrame()

    if symbol not in tick_index.index[broker_type]:
        log_buffer.append(
            f"  ⚠️ Symbol '{symbol}' not found in tick index for broker_type '{broker_type}'")
        return pd.DataFrame()

    tick_files = [
        Path(entry['path'])
        for entry in tick_index.index[broker_type][symbol]
    ]

    dfs = []
    for tick_file in tick_files:
        df = read_tick_parquet(tick_file)
        dfs.append(df)

    if not dfs:
        return pd.DataFrame()

    combined = pd.concat(dfs, ignore_index=True)
    combined = combined.sort_values('timestamp').reset_index(drop=True)

    return combined


def _write_bar_file(
    data_dir: Path,
    symbol: str,
    timeframe: str,
    bars_df: pd.DataFrame,
    broker_type: str,
    importer_version: str,
    source_version_min: str = '1.0.0',
    source_version_max: str = '1.0.0',
    log_buffer: list[str] = None
) -> None:
    """
    Write bar DataFrame to parquet file.

    Args:
        data_dir: Base data directory
        symbol: Trading symbol
        timeframe: Timeframe string
        bars_df: Bar DataFrame
        broker_type: Broker type identifier
        importer_version: BarImporter version string
        source_version_min: Minimum source data version
        source_version_max: Maximum source data version
        log_buffer: Buffer for log messages
    """
    bars_dir = data_dir / broker_type / "bars" / symbol
    bars_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{symbol}_{timeframe}_BARS.parquet"
    filepath = bars_dir / filename

    market_config = MarketConfigManager()
    market_type = market_config.get_market_type(broker_type)

    metadata = {
        'symbol': symbol,
        'timeframe': timeframe,
        'broker_type': broker_type,
        'market_type': market_type.value,
        'bar_count': str(len(bars_df)),
        'start_time': bars_df['timestamp'].min().isoformat(),
        'end_time': bars_df['timestamp'].max().isoformat(),
        'importer_version': importer_version,
        'rendered_at': pd.Timestamp.now(tz='UTC').isoformat(),
        'source_version_min': source_version_min,
        'source_version_max': source_version_max,
    }

    table = pa.Table.from_pandas(bars_df)
    table = table.replace_schema_metadata(metadata)
    pq.write_table(table, filepath, compression="snappy")

    file_size_mb = filepath.stat().st_size / (1024 * 1024)
    if log_buffer is not None:
        log_buffer.append(
            f"    ├─ Written: {filename} "
            f"({len(bars_df):,} bars, {file_size_mb:.2f} MB)"
        )


def _extract_source_versions(
    tick_files: List[Path],
    log_buffer: list[str]
) -> Tuple[str, str]:
    """
    Extract min/max data_format_version from tick parquet files.

    Args:
        tick_files: List of tick parquet file paths
        log_buffer: Buffer for log messages

    Returns:
        Tuple (version_min, version_max)
    """
    versions = []

    for tick_file in tick_files:
        try:
            pq_file = pq.ParquetFile(tick_file)
            metadata_raw = pq_file.metadata.metadata

            version = metadata_raw.get(b'data_format_version', b'1.0.0')
            if isinstance(version, bytes):
                version = version.decode('utf-8')

            # Fallback: Try collector_version (legacy field)
            if version == '1.0.0':
                legacy = metadata_raw.get(b'collector_version', b'1.0.0')
                if isinstance(legacy, bytes):
                    legacy = legacy.decode('utf-8')
                version = legacy

            versions.append(version)

        except Exception as e:
            log_buffer.append(
                f"  ⚠️ Could not extract version from {tick_file.name}: {e}")
            versions.append('1.0.0')

    if not versions:
        return ('1.0.0', '1.0.0')

    sorted_versions = sorted(set(versions), key=lambda v: [
        int(x) for x in v.split('.')])
    return (sorted_versions[0], sorted_versions[-1])


# =============================================================================
# BAR IMPORTER CLASS
# =============================================================================

class BarImporter:
    """
    Main orchestrator for bar pre-rendering system.

    Renders bars from tick data and saves them as parquet files.
    One file per timeframe per symbol.
    """

    VERSION = "1.1"  # Updated for broker_type-first index structure

    def __init__(self, data_dir: Optional[str] = None):
        """
        Initialize Bar importer.

        Args:
            data_dir: Override data directory (default: from ImportConfigManager)
        """
        self._import_config = ImportConfigManager()

        if data_dir:
            self.data_dir = Path(data_dir)
        else:
            self.data_dir = Path(self._import_config.get_import_output_path())
        if not self.data_dir.exists():
            raise FileNotFoundError(
                f"Data directory not found: {self.data_dir}")

        # Initialize tick index for finding tick files
        self.tick_index = TickIndexManager(data_dir=str(self.data_dir))
        self.tick_index.build_index()

        # Statistics
        self.processed_symbols = 0
        self.total_bars_rendered = 0
        self.errors = []

    def render_bars_for_all_symbols(
        self,
        broker_types: List[str],
        clean_mode: bool = False
    ):
        """
        Render bars for ALL symbols across one or more broker_types.

        All symbols from all broker_types are rendered in a single pool.
        Index/cache rebuild is NOT performed — caller is responsible.

        Args:
            broker_types: List of broker type identifiers (e.g., ['mt5', 'kraken_spot'])
            clean_mode: If True, delete all existing bars before rendering
        """
        max_workers = self._import_config.get_bar_render_workers()

        vLog.info("\n" + "=" * 80)
        vLog.info(
            f"Bar Pre-Rendering - Batch Mode ({', '.join(broker_types)})")
        vLog.info(f"Workers: {max_workers}")
        vLog.info("=" * 80)

        # === CLEAN MODE: Delete all bars first ===
        if clean_mode:
            for bt in broker_types:
                self._clean_bars(bt)

        # === COLLECT ALL (broker_type, symbol) PAIRS ===
        all_tasks: List[Tuple[str, str]] = []
        for bt in broker_types:
            symbols = self.tick_index.list_symbols(bt)
            if not symbols:
                vLog.warning(
                    f"No symbols found in tick data for broker_type '{bt}'!")
                continue
            for symbol in symbols:
                all_tasks.append((bt, symbol))

        if not all_tasks:
            vLog.warning('No symbols found across any broker_type!')
            return

        vLog.info(
            f"Found {len(all_tasks)} symbols to process across "
            f"{len(broker_types)} broker_types"
        )
        vLog.info("=" * 80 + "\n")

        # === PARALLEL RENDERING ===
        effective_workers = min(max_workers, len(all_tasks))

        if effective_workers <= 1:
            self._render_sequential(all_tasks)
        else:
            self._render_parallel(all_tasks, effective_workers)

        # Print summary
        self._print_summary()

    def _render_sequential(self, tasks: List[Tuple[str, str]]) -> None:
        """
        Render symbols sequentially (single-process fallback).

        Args:
            tasks: List of (broker_type, symbol) tuples
        """
        for i, (broker_type, symbol) in enumerate(tasks, 1):
            vLog.info(
                f"\n[{i}/{len(tasks)}] Processing {broker_type}/{symbol}...")

            result = _render_symbol_worker(
                symbol, broker_type, str(self.data_dir), self.VERSION
            )

            # Flush log buffer
            for line in result.log_buffer:
                vLog.info(line)

            if result.success:
                self.processed_symbols += 1
                self.total_bars_rendered += result.bars_rendered
            else:
                vLog.error(result.error_message)
                self.errors.append(result.error_message)

    def _render_parallel(
        self,
        tasks: List[Tuple[str, str]],
        max_workers: int
    ) -> None:
        """
        Render symbols in parallel using ProcessPoolExecutor.

        Args:
            tasks: List of (broker_type, symbol) tuples
            max_workers: Number of worker processes
        """
        vLog.info(
            f"🚀 Launching {max_workers} worker processes for "
            f"{len(tasks)} symbols...\n"
        )

        results: list[BarRenderResult] = []

        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            future_to_task = {
                executor.submit(
                    _render_symbol_worker,
                    symbol, broker_type, str(self.data_dir), self.VERSION
                ): (broker_type, symbol)
                for broker_type, symbol in tasks
            }

            for future in as_completed(future_to_task):
                broker_type, symbol = future_to_task[future]
                try:
                    result = future.result()
                    results.append(result)
                except Exception as e:
                    error_msg = f"ERROR in {broker_type}/{symbol}: {str(e)}"
                    results.append(BarRenderResult(
                        symbol=symbol,
                        broker_type=broker_type,
                        success=False,
                        error_message=error_msg,
                        log_buffer=[f"  └─ ❌ Worker crashed: {str(e)}"]
                    ))

        # Flush all log buffers sorted by broker_type/symbol
        results.sort(key=lambda r: (r.broker_type, r.symbol))
        for i, result in enumerate(results, 1):
            vLog.info(
                f"\n[{i}/{len(results)}] {result.broker_type}/{result.symbol}:")
            for line in result.log_buffer:
                vLog.info(line)

            if result.success:
                self.processed_symbols += 1
                self.total_bars_rendered += result.bars_rendered
            else:
                vLog.error(result.error_message)
                self.errors.append(result.error_message)

    def _clean_bars(self, broker_type: str) -> None:
        """
        Delete all existing bar files for a broker_type.

        Args:
            broker_type: Broker type identifier
        """
        bars_base_dir = self.data_dir / broker_type / "bars"

        if bars_base_dir.exists():
            vLog.warning(
                f"🗑️  CLEAN MODE: Deleting all bars in {bars_base_dir}")

            deleted_count = 0
            for bar_file in bars_base_dir.glob("**/*_BARS.parquet"):
                bar_file.unlink()
                deleted_count += 1

            vLog.info(f"   Deleted {deleted_count} bar files")

            # Clean up empty directories
            for symbol_dir in bars_base_dir.iterdir():
                if symbol_dir.is_dir() and not any(symbol_dir.iterdir()):
                    symbol_dir.rmdir()
                    vLog.debug(
                        f"   Removed empty directory: {symbol_dir.name}")
        else:
            vLog.info(f"   No bars directory found - nothing to clean")

    def render_bars_for_symbol(
        self,
        symbol: str,
        broker_type: str
    ):
        """
        Render bars for a single symbol (convenience method).

        Args:
            symbol: Trading symbol (e.g., 'EURUSD')
            broker_type: Broker type identifier - REQUIRED
        """
        result = _render_symbol_worker(
            symbol, broker_type, str(self.data_dir), self.VERSION
        )

        # Flush log buffer
        for line in result.log_buffer:
            vLog.info(line)

        if result.success:
            self.processed_symbols += 1
            self.total_bars_rendered += result.bars_rendered
        else:
            vLog.error(result.error_message)
            self.errors.append(result.error_message)

    def update_bar_index(self):
        """
        Update bar index and discovery caches after rendering.

        Creates/updates .parquet_bars_index.parquet and rebuilds discovery caches.
        Called by the caller after rendering is complete — not automatically.
        """
        vLog.info("\n📄 Updating bar index...")
        try:
            bar_index = BarsIndexManager()
            bar_index.build_index(force_rebuild=True)

            # Count symbols across all broker_types
            total_symbols = len(bar_index.list_symbols())
            broker_types = bar_index.list_broker_types()
            vLog.info(
                f"✅ Bar index updated: {total_symbols} symbols across "
                f"{len(broker_types)} broker_types ({', '.join(broker_types)})"
            )

            # Rebuild all discovery caches
            DiscoveryCacheManager().rebuild_all(force=True)
            vLog.info(f"✅ Discovery caches rebuilt")

        except ImportError as e:
            vLog.error(f"❌ Failed to import BarsIndexManager: {e}")
            vLog.error("   Make sure bars_index_manager.py is available")
            vLog.error("   You can manually build the index later.")
        except Exception as e:
            vLog.error(f"❌ Failed to update bar index: {e}")
            vLog.error("   Index may be outdated - run manual rebuild!")

    def _print_summary(self):
        """Print processing summary"""
        vLog.info("\n" + "=" * 80)
        vLog.info("BAR RENDERING SUMMARY")
        vLog.info("=" * 80)
        vLog.info(f"✅ Processed Symbols: {self.processed_symbols}")
        vLog.info(f"✅ Rendered Bars: {self.total_bars_rendered:,}")
        vLog.info(f"❌ Errors: {len(self.errors)}")

        if self.errors:
            vLog.error("\nERROR LIST:")
            for error in self.errors:
                vLog.error(f"  - {error}")

        vLog.info("=" * 80 + "\n")
