"""
Bar Importer - Pre-Render Bars from Tick Data
==============================================

Orchestrates bar pre-rendering for all symbols and timeframes.
Called automatically after tick import or manually for re-rendering.

Workflow:
1. Load ALL tick files for a symbol
2. Call VectorizedBarRenderer for all timeframes
3. Write bar parquet files (one per timeframe)
4. Update bar index

REFACTORED: broker_type is now required parameter (no default)
INDEX STRUCTURE: {broker_type: {symbol: [files]}}
"""

from pathlib import Path
from typing import List
import time

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from python.configuration.app_config_manager import AppConfigManager
from python.configuration.market_config_manager import MarketConfigManager
from python.data_management.importers.vectorized_bar_renderer import VectorizedBarRenderer
from python.data_management.index.tick_index_manager import TickIndexManager
from python.data_management.index.bars_index_manager import BarsIndexManager


from python.framework.logging.bootstrap_logger import get_global_logger
from python.framework.reporting.coverage_report_cache import CoverageReportCache
vLog = get_global_logger()


class BarImporter:
    """
    Main orchestrator for bar pre-rendering system.

    Renders bars from tick data and saves them as parquet files.
    One file per timeframe per symbol.
    """

    VERSION = "1.1"  # Updated for broker_type-first index structure

    def __init__(self):
        """
        Initialize Bar importer with paths from AppConfigManager.
        """
        app_config = AppConfigManager()
        self.data_dir = Path(app_config.get_data_processed_path())
        if not self.data_dir.exists():
            raise FileNotFoundError(
                f"Data directory not found: {self.data_dir}")

        # Initialize tick index for finding tick files
        self.tick_index = TickIndexManager()
        self.tick_index.build_index()

        # Statistics
        self.processed_symbols = 0
        self.total_bars_rendered = 0
        self.errors = []

    def render_bars_for_all_symbols(self, broker_type: str, clean_mode: bool = False):
        """
        Render bars for ALL symbols found in tick data for a specific broker_type.

        Args:
            broker_type: Broker type identifier (e.g., 'mt5', 'kraken_spot') - REQUIRED
            clean_mode: If True, delete all existing bars before rendering
        """
        vLog.info("\n" + "=" * 80)
        vLog.info(
            f"Bar Pre-Rendering - Batch Mode (broker_type: {broker_type})")
        vLog.info("=" * 80)

        # === CLEAN MODE: Delete all bars first ===
        if clean_mode:
            bars_base_dir = self.data_dir / broker_type / "bars"

            if bars_base_dir.exists():
                vLog.warning(
                    f"🗑️  CLEAN MODE: Deleting all bars in {bars_base_dir}")

                # Delete all bar files
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

        # Get all symbols from tick index FOR THIS BROKER_TYPE
        # Use broker_type parameter for list_symbols()
        symbols = self.tick_index.list_symbols(broker_type)

        if not symbols:
            vLog.warning(
                f"No symbols found in tick data for broker_type '{broker_type}'!")
            return

        vLog.info(f"Found {len(symbols)} symbols to process for {broker_type}")
        vLog.info("=" * 80 + "\n")

        # Process each symbol
        for i, symbol in enumerate(symbols, 1):
            vLog.info(
                f"\n[{i}/{len(symbols)}] Processing {broker_type}/{symbol}...")
            try:
                self.render_bars_for_symbol(symbol, broker_type)
                self.processed_symbols += 1
            except Exception as e:
                error_msg = f"ERROR in {broker_type}/{symbol}: {str(e)}"
                vLog.error(error_msg)
                self.errors.append(error_msg)

        # Update bar index after all symbols processed
        self._update_bar_index()

        # Print summary
        self._print_summary()

    def render_bars_for_symbol(
        self,
        symbol: str,
        broker_type: str
    ):
        """
        Render bars for a single symbol.

        Args:
            symbol: Trading symbol (e.g., 'EURUSD')
            broker_type: Broker type identifier - REQUIRED
        """
        start_time = time.time()

        # === 1. LOAD TICK DATA ===
        vLog.info(f"  ├─ Loading tick data for {broker_type}/{symbol}...")
        ticks_df = self._load_all_ticks_for_symbol(symbol, broker_type)

        if ticks_df.empty:
            vLog.warning(f"  └─ No tick data found for {broker_type}/{symbol}")
            return

        vLog.info(f"  ├─ Loaded {len(ticks_df):,} ticks")

        # === 1.5 EXTRACT SOURCE VERSIONS ===
        # Access index with broker_type first
        tick_files = [
            Path(entry['path'])
            for entry in self.tick_index.index[broker_type][symbol]
        ]
        source_version_min, source_version_max = self._extract_source_versions(
            tick_files)
        vLog.debug(
            f"  ├─ Source versions: {source_version_min} - {source_version_max}")

        # === 2. RENDER BARS ===
        vLog.info(f"  ├─ Rendering bars...")
        renderer = VectorizedBarRenderer(symbol)
        all_bars = renderer.render_all_timeframes(ticks_df, fill_gaps=True)

        # === 3. WRITE BAR FILES ===
        vLog.info(f"  ├─ Writing bar files...")
        bars_written = 0

        for timeframe, bars_df in all_bars.items():
            if len(bars_df) > 0:
                self._write_bar_file(
                    symbol,
                    timeframe,
                    bars_df,
                    broker_type,
                    source_version_min,
                    source_version_max
                )
                bars_written += len(bars_df)
                self.total_bars_rendered += len(bars_df)

        # === 4. LOG STATISTICS ===
        elapsed = time.time() - start_time
        vLog.info(
            f"  └─ ✅ {broker_type}/{symbol}: {bars_written:,} bars across "
            f"{len(all_bars)} timeframes in {elapsed:.2f}s"
        )

    def _load_all_ticks_for_symbol(
        self,
        symbol: str,
        broker_type: str
    ) -> pd.DataFrame:
        """
        Load ALL tick files for a symbol.

        Args:
            symbol: Trading symbol
            broker_type: Broker type identifier

        Returns:
            DataFrame with all ticks for symbol
        """
        # Check broker_type exists in index first
        if broker_type not in self.tick_index.index:
            vLog.warning(
                f"Broker type '{broker_type}' not found in tick index")
            return pd.DataFrame()

        # Check symbol exists for this broker_type
        if symbol not in self.tick_index.index[broker_type]:
            vLog.warning(
                f"Symbol '{symbol}' not found in tick index for broker_type '{broker_type}'"
            )
            return pd.DataFrame()

        # Access with broker_type first
        tick_files = [
            Path(entry['path'])
            for entry in self.tick_index.index[broker_type][symbol]
        ]

        # Load and concatenate all files
        dfs = []
        for tick_file in tick_files:
            df = pd.read_parquet(tick_file)
            dfs.append(df)

        if not dfs:
            return pd.DataFrame()

        # Combine and sort
        combined = pd.concat(dfs, ignore_index=True)
        combined = combined.sort_values('timestamp').reset_index(drop=True)

        return combined

    def _write_bar_file(
        self,
        symbol: str,
        timeframe: str,
        bars_df: pd.DataFrame,
        broker_type: str,
        source_version_min: str = '1.0.0',
        source_version_max: str = '1.0.0'
    ):
        """
        Write bar DataFrame to parquet file.

        Args:
            symbol: Trading symbol
            timeframe: Timeframe string
            bars_df: Bar DataFrame
            broker_type: Broker type identifier
            source_version_min: Minimum source data version
            source_version_max: Maximum source data version
        """
        bars_dir = self.data_dir / broker_type / "bars" / symbol
        bars_dir.mkdir(parents=True, exist_ok=True)

        filename = f"{symbol}_{timeframe}_BARS.parquet"
        filepath = bars_dir / filename

        # Get market_type from MarketConfigManager
        market_config = MarketConfigManager()
        market_type = market_config.get_market_type(broker_type)

        metadata = {
            'symbol': symbol,
            'timeframe': timeframe,
            'broker_type': broker_type,
            'market_type': market_type.value,  # NEW: Correct market_type from config
            'bar_count': str(len(bars_df)),
            'start_time': bars_df['timestamp'].min().isoformat(),
            'end_time': bars_df['timestamp'].max().isoformat(),
            'importer_version': self.VERSION,
            'rendered_at': pd.Timestamp.now(tz='UTC').isoformat(),
            'source_version_min': source_version_min,
            'source_version_max': source_version_max,
        }

        # Write parquet with metadata
        table = pa.Table.from_pandas(bars_df)
        table = table.replace_schema_metadata(metadata)
        pq.write_table(table, filepath, compression="snappy")

        # Log file size
        file_size_mb = filepath.stat().st_size / (1024 * 1024)
        vLog.debug(
            f"    ├─ Written: {filename} "
            f"({len(bars_df):,} bars, {file_size_mb:.2f} MB)"
        )

    def _extract_source_versions(self, tick_files: List[Path]) -> tuple:
        """
        Extract min/max data_format_version from tick parquet files.

        Args:
            tick_files: List of tick parquet file paths

        Returns:
            Tuple (version_min, version_max)
        """
        versions = []

        for tick_file in tick_files:
            try:
                pq_file = pq.ParquetFile(tick_file)
                metadata_raw = pq_file.metadata.metadata

                # Extract version from metadata
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
                vLog.warning(
                    f"Could not extract version from {tick_file.name}: {e}")
                versions.append('1.0.0')

        if not versions:
            return ('1.0.0', '1.0.0')

        # Sort versions for min/max
        sorted_versions = sorted(set(versions), key=lambda v: [
            int(x) for x in v.split('.')])
        return (sorted_versions[0], sorted_versions[-1])

    def _update_bar_index(self):
        """
        Update bar index after rendering.

        Creates/updates .parquet_bars_index.json
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

            # Coverage Cache rebuilden
            CoverageReportCache().build_all(force_rebuild=True)
            vLog.info(f"✅ Coverage cache index updated")

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
