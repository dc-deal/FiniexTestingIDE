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

Directory Structure:
- data/parquet/mt5/ticks/EURUSD/*.parquet  → Input
- data/parquet/mt5/bars/EURUSD/EURUSD_M5_BARS.parquet → Output
"""

from pathlib import Path
from typing import List, Optional
import time

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from python.data_worker.importer.vectorized_bar_renderer import VectorizedBarRenderer
from python.data_worker.data_loader.tick_index_manager import TickIndexManager
from python.data_worker.data_loader.bars_index_manager import BarsIndexManager


from python.components.logger.bootstrap_logger import get_logger
vLog = get_logger()


class BarImporter:
    """
    Main orchestrator for bar pre-rendering system.

    Renders bars from tick data and saves them as parquet files.
    One file per timeframe per symbol.
    """

    VERSION = "1.0"

    # Supported timeframes (from types.py)
    SUPPORTED_TIMEFRAMES = ['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1']

    def __init__(self, data_dir: str = "./data/parquet/"):
        """
        Initialize Bar Importer.

        Args:
            data_dir: Root data directory (default: ./data/parquet/)
        """
        self.data_dir = Path(data_dir)
        if not self.data_dir.exists():
            raise FileNotFoundError(f"Data directory not found: {data_dir}")

        # Initialize tick index for finding tick files
        self.tick_index = TickIndexManager(self.data_dir)
        self.tick_index.build_index()

        # Statistics
        self.processed_symbols = 0
        self.total_bars_rendered = 0
        self.errors = []

    def render_bars_for_all_symbols(self, data_collector: str = "mt5", clean_mode: bool = False):
        """
            Render bars for ALL symbols found in tick data.

            Use this after bulk tick import to pre-render everything.

            Args:
                data_collector: Data collector name (default: 'mt5')
                clean_mode: If True, delete all existing bars before rendering (default: False)
            """
        vLog.info("\n" + "=" * 80)
        vLog.info(f"Bar Pre-Rendering - Batch Mode")
        vLog.info("=" * 80)

        # === CLEAN MODE: Delete all bars first ===
        if clean_mode:
            bars_base_dir = self.data_dir / data_collector / "bars"

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

        # Get all symbols from tick index
        symbols = self.tick_index.list_symbols()

        if not symbols:
            vLog.warning("No symbols found in tick data!")
            return

        vLog.info(f"Found {len(symbols)} symbols to process")
        vLog.info("=" * 80 + "\n")

        # Process each symbol
        for i, symbol in enumerate(symbols, 1):
            vLog.info(f"\n[{i}/{len(symbols)}] Processing {symbol}...")
            try:
                self.render_bars_for_symbol(symbol, data_collector)
                self.processed_symbols += 1
            except Exception as e:
                error_msg = f"FEHLER bei {symbol}: {str(e)}"
                vLog.error(error_msg)
                self.errors.append(error_msg)

        # Update bar index after all symbols processed
        self._update_bar_index()

        # Print summary
        self._print_summary()

    def render_bars_for_symbol(
        self,
        symbol: str,
        data_collector: str = "mt5"
    ):
        """
        Render bars for a single symbol.

        Steps:
        1. Load ALL tick files for symbol
        2. Render bars for all timeframes
        3. Write bar parquet files
        4. Log statistics

        Args:
            symbol: Trading symbol (e.g., 'EURUSD')
            data_collector: Data collector name (default: 'mt5')
        """
        start_time = time.time()

        # === 1. LOAD TICK DATA ===
        vLog.info(f"  ├─ Loading tick data for {symbol}...")
        ticks_df = self._load_all_ticks_for_symbol(symbol, data_collector)

        if ticks_df.empty:
            vLog.warning(f"  └─ No tick data found for {symbol}")
            return

        vLog.info(f"  ├─ Loaded {len(ticks_df):,} ticks")

        # === 1.5 EXTRACT SOURCE VERSIONS ===
        tick_files = [Path(entry['path'])
                      for entry in self.tick_index.index[symbol]]
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
                    data_collector,
                    source_version_min,
                    source_version_max
                )
                bars_written += len(bars_df)
                self.total_bars_rendered += len(bars_df)

        # === 4. LOG STATISTICS ===
        elapsed = time.time() - start_time
        vLog.info(
            f"  └─ ✅ {symbol}: {bars_written:,} bars across "
            f"{len(all_bars)} timeframes in {elapsed:.2f}s"
        )

    def _load_all_ticks_for_symbol(
        self,
        symbol: str,
        data_collector: str
    ) -> pd.DataFrame:
        """
        Load ALL tick files for a symbol.

        Args:
            symbol: Trading symbol
            data_collector: Data collector name

        Returns:
            DataFrame with all ticks for symbol
        """
        # Get all tick files for symbol from index
        if symbol not in self.tick_index.index:
            vLog.warning(f"Symbol {symbol} not found in tick index")
            return pd.DataFrame()

        tick_files = [
            Path(entry['path'])
            for entry in self.tick_index.index[symbol]
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
        data_collector: str,
        source_version_min: str = '1.0.0',
        source_version_max: str = '1.0.0'
    ):
        """
        Write bar DataFrame to parquet file.

        File structure: data_collector/bars/symbol/SYMBOL_TF_BARS.parquet
        Example: mt5/bars/EURUSD/EURUSD_M5_BARS.parquet

        Args:
            symbol: Trading symbol
            timeframe: Timeframe string
            bars_df: Bar DataFrame
            data_collector: Data collector name
        """
        # Create directory structure
        bars_dir = self.data_dir / data_collector / "bars" / symbol
        bars_dir.mkdir(parents=True, exist_ok=True)

        # Filename: SYMBOL_TIMEFRAME_BARS.parquet
        filename = f"{symbol}_{timeframe}_BARS.parquet"
        filepath = bars_dir / filename

        # Prepare metadata
        metadata = {
            'symbol': symbol,
            'timeframe': timeframe,
            'data_collector': data_collector,
            'bar_count': str(len(bars_df)),
            'start_time': bars_df['timestamp'].min().isoformat(),
            'end_time': bars_df['timestamp'].max().isoformat(),
            'importer_version': self.VERSION,
            'rendered_at': pd.Timestamp.now(tz='UTC').isoformat(),
            # Source version tracking
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
            # Try to import bar index manager
            # NOTE: File must be at python/data_worker/data_loader/parquet_bars_index.py

            bar_index = BarsIndexManager(self.data_dir)
            bar_index.build_index(force_rebuild=True)

            symbols = bar_index.list_symbols()
            vLog.info(f"✅ Bar index updated: {len(symbols)} symbols indexed")

        except ImportError as e:
            vLog.error(f"❌ Failed to import BarsIndexManager: {e}")
            vLog.error("   Make sure parquet_bars_index.py is at:")
            vLog.error(
                "   python/data_worker/data_loader/parquet_bars_index.py")
            vLog.error("   You can manually build the index later.")
        except Exception as e:
            vLog.error(f"❌ Failed to update bar index: {e}")
            vLog.error("   Index may be outdated - run manual rebuild!")

    def _print_summary(self):
        """Print processing summary"""
        vLog.info("\n" + "=" * 80)
        vLog.info("BAR RENDERING ZUSAMMENFASSUNG")
        vLog.info("=" * 80)
        vLog.info(f"✅ Verarbeitete Symbols: {self.processed_symbols}")
        vLog.info(f"✅ Gerenderte Bars: {self.total_bars_rendered:,}")
        vLog.info(f"❌ Fehler: {len(self.errors)}")

        if self.errors:
            vLog.error("\nFEHLER-LISTE:")
            for error in self.errors:
                vLog.error(f"  - {error}")

        vLog.info("=" * 80 + "\n")


# =============================================================================
# CLI INTERFACE (Optional - for future use)
# =============================================================================

def main():
    """
    CLI entry point for manual bar rendering.

    Usage:
        python -m bar_importer                    # Render all symbols
        python -m bar_importer --symbol EURUSD    # Render specific symbol
    """
    import argparse

    parser = argparse.ArgumentParser(
        description='Pre-render bars from tick data'
    )
    parser.add_argument(
        '--symbol',
        type=str,
        help='Specific symbol to render (default: all)'
    )
    parser.add_argument(
        '--collector',
        type=str,
        default='mt5',
        help='Data collector name (default: mt5)'
    )

    args = parser.parse_args()

    importer = BarImporter()

    if args.symbol:
        # Render specific symbol
        vLog.info(f"Rendering bars for {args.symbol}...")
        importer.render_bars_for_symbol(args.symbol, args.collector)
    else:
        # Render all symbols
        importer.render_bars_for_all_symbols(args.collector)


if __name__ == '__main__':
    main()
