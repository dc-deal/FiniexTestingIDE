"""
FiniexTestingIDE Data Loader - Core Module
Pure loading logic: Fast, focused, zero dependencies on analysis

EXTENDED (C#002): Integration with ParquetIndexManager for optimized loading
UPDATED (C#003): Support for hierarchical directory structure
"""

from python.components.logger.bootstrap_logger import setup_logging
from datetime import timezone
from pathlib import Path
from typing import List, Optional

import pandas as pd
import pyarrow.parquet as pq

# Existing imports
from python.data_worker.data_loader.exceptions import (
    ArtificialDuplicateException,
    DuplicateReport,
    InvalidDataModeException
)

# NEW (C#002): Index integration
from python.data_worker.data_loader.parquet_index import ParquetIndexManager
from python.framework.utils.market_calendar import MarketCalendar

vLog = setup_logging(name="StrategyRunner")


class TickDataLoader:
    """
    Core tick data loading with caching and filtering

    Responsibilities:
    - Load parquet files from disk
    - Cache loaded data for performance
    - Apply date range filters
    - Remove duplicates based on data_mode
    - Detect artificial duplicates (data integrity)

    EXTENDED (C#002):
    - Uses ParquetIndexManager for optimized file selection
    - Only loads files that intersect with requested time range
    - 10x faster loading for time-filtered queries

    UPDATED (C#003):
    - Supports hierarchical directory structure (data_collector/symbol/)

    Design: Minimal, fast, no analysis logic
    """

    # Valid data modes
    VALID_DATA_MODES = ["raw", "realistic", "clean"]

    def __init__(self, data_dir: str = "./data/processed/"):
        """
        Initialize data loader

        Args:
            data_dir: Directory containing parquet files

        Raises:
            FileNotFoundError: If data directory doesn't exist
        """
        self.data_dir = Path(data_dir)
        if not self.data_dir.exists():
            raise FileNotFoundError(f"Data directory not found: {data_dir}")

        self._symbol_cache = {}

        # NEW (C#002): Initialize index manager
        self.index_manager = ParquetIndexManager(self.data_dir)
        self.index_manager.build_index()  # Auto-build/load on init

    def list_available_symbols(self) -> List[str]:
        """
        List all available symbols in data directory

        OPTIMIZED (C#002): Uses index instead of file scanning

        Returns:
            Sorted list of symbol names (e.g. ['EURUSD', 'GBPUSD', 'USDJPY'])
        """
        return self.index_manager.list_symbols()

    def load_symbol_data(
        self,
        symbol: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        data_mode: str = "realistic",
        use_cache: bool = True,
        detect_artificial_duplicates: bool = True,
    ) -> pd.DataFrame:
        """
        Load tick data for a symbol with optional date filtering

        OPTIMIZED (C#002): Uses index for precise file selection

        Performance improvement:
        - Before: Loads ALL files for symbol, then filters
        - After: Loads ONLY files intersecting time range
        - Speedup: ~10x for time-filtered queries

        Args:
            symbol: Currency pair (e.g. 'EURUSD')
            start_date: Start date filter (ISO format or datetime)
            end_date: End date filter (ISO format or datetime)
            data_mode: Data quality mode ('raw', 'realistic', 'clean')
            use_cache: Enable caching (default: True)
            detect_artificial_duplicates: Check for duplicate imports

        Returns:
            DataFrame with tick data

        Raises:
            InvalidDataModeException: If data_mode is invalid
            ArtificialDuplicateException: If duplicate files detected
        """
        # Validate data mode
        if data_mode not in self.VALID_DATA_MODES:
            raise InvalidDataModeException(data_mode, self.VALID_DATA_MODES)

        # Cache key
        cache_key = f"{symbol}_{start_date}_{end_date}_{data_mode}"

        if use_cache and cache_key in self._symbol_cache:
            vLog.debug(f"📦 Cache hit for {cache_key}")
            return self._symbol_cache[cache_key].copy()

        # NEW (C#002): Index-based file selection
        if start_date and end_date:
            # Convert to datetime for index query
            start_dt = pd.to_datetime(start_date)
            end_dt = pd.to_datetime(end_date)

            # Use index to find relevant files
            files = self.index_manager.get_relevant_files(
                symbol, start_dt, end_dt)

            # Get total file count for comparison
            all_files_count = len(self.index_manager.index.get(symbol, []))

            if files:
                vLog.info(
                    f"📊 Loading {len(files)}/{all_files_count} files for {symbol} "
                    f"({MarketCalendar.format_time_range(start_date, end_date)})"
                )

            else:
                vLog.warning(f"No files found for {symbol} in date range")
                return pd.DataFrame()
        else:
            # No date filter: load all files (fallback to legacy method)
            files = self._get_symbol_files(symbol)
            vLog.info(f"📊 Loading all {len(files)} files for {symbol}")

        if not files:
            vLog.warning(f"No Parquet files found for {symbol}")
            return pd.DataFrame()

        # Optional: Detect artificial duplicates
        if detect_artificial_duplicates:
            duplicate_report = self._check_artificial_duplicates(files)
            if duplicate_report:
                raise ArtificialDuplicateException(duplicate_report)

        # Load all files into DataFrame
        dfs = []
        for file in files:
            df = pd.read_parquet(file)
            dfs.append(df)

        combined_df = pd.concat(dfs, ignore_index=True)

        # Sort by timestamp (critical for backtesting)
        combined_df = combined_df.sort_values(
            "timestamp").reset_index(drop=True)

        # Handle natural duplicates based on data_mode
        if data_mode in ["realistic", "clean"]:
            initial_count = len(combined_df)
            combined_df = combined_df.drop_duplicates(
                subset=["time_msc", "bid", "ask"], keep="last"
            )
            duplicates_removed = initial_count - len(combined_df)

            if duplicates_removed > 0:
                duplicate_percentage = (
                    duplicates_removed / initial_count) * 100
                vLog.info(
                    f"🔁 Removed {duplicates_removed:,} natural duplicates ")
                vLog.info(
                    f"from {initial_count:,} total ticks ({duplicate_percentage:.2f}% of data) [data_mode={data_mode}]"
                )
            else:
                vLog.info(
                    f"🔁 No natural duplicates found in {initial_count:,} ticks")
        else:
            vLog.info(
                f"🔁 Keeping all ticks including natural duplicates [data_mode={data_mode}]"
            )

        # Apply date filters (now more efficient as we pre-filtered files)
        combined_df = self._apply_date_filters(
            combined_df, start_date, end_date)

        # Update cache
        if use_cache:
            self._symbol_cache[cache_key] = combined_df.copy()

        vLog.info(f"✅ Loaded: {len(combined_df):,} ticks for {symbol}")
        return combined_df

    # =========================================================================
    # EXISTING METHODS (Unchanged)
    # =========================================================================

    def _get_symbol_files(self, symbol: str) -> List[Path]:
        """
        Get all Parquet files for a symbol (legacy method).

        Used as fallback when no date filter specified.

        UPDATED (C#003): Searches in hierarchical structure
        """
        # CHANGED (C#003): Recursive pattern for hierarchical structure
        # Before: f"{symbol}_*.parquet"
        # Now: f"**/{symbol}/*.parquet" - searches in all data_collector subdirs
        pattern = f"**/{symbol}/{symbol}_*.parquet"
        files = list(self.data_dir.glob(pattern))
        return sorted(files)

    def _check_artificial_duplicates(self, files: List[Path]) -> Optional[DuplicateReport]:
        """
        Check for artificial duplicates via Parquet metadata

        Artificial duplicates occur when multiple Parquet files reference
        the same source JSON file (e.g. through manual file copying).

        This is different from natural duplicates (same tick data from broker).
        """
        if not files:
            return None

        source_files = {}

        for file in files:
            try:
                pq_file = pq.ParquetFile(file)
                metadata_raw = pq_file.metadata.metadata

                # Extract source_file
                source_file = metadata_raw.get(
                    b'source_file', b'').decode('utf-8')

                if source_file in source_files:
                    # DUPLICATE DETECTED!
                    existing_file = source_files[source_file]

                    vLog.error(
                        f"❌ ARTIFICIAL DUPLICATE DETECTED!")
                    vLog.error(
                        f"   Source: {source_file}")
                    vLog.error(
                        f"   File 1: {existing_file.name}")
                    vLog.error(
                        f"   File 2: {file.name}")

                    # Build duplicate report
                    duplicate_files = [existing_file, file]
                    tick_counts = []
                    time_ranges = []
                    file_sizes_mb = []
                    metadata_list = []

                    for dup_file in duplicate_files:
                        df = pd.read_parquet(dup_file)
                        pq_file = pq.ParquetFile(dup_file)
                        metadata_raw = pq_file.metadata.metadata

                        tick_counts.append(len(df))
                        time_ranges.append(
                            (df['timestamp'].min(), df['timestamp'].max())
                        )
                        file_sizes_mb.append(
                            dup_file.stat().st_size / (1024 * 1024)
                        )
                        metadata_list.append({
                            key.decode('utf-8') if isinstance(key, bytes) else key:
                            value.decode(
                                'utf-8') if isinstance(value, bytes) else value
                            for key, value in metadata_raw.items()
                        })

                    return DuplicateReport(
                        source_file=source_file,
                        duplicate_files=duplicate_files,
                        tick_counts=tick_counts,
                        time_ranges=time_ranges,
                        file_sizes_mb=file_sizes_mb,
                        metadata=metadata_list
                    )

                source_files[source_file] = file

            except Exception as e:
                vLog.warning(
                    f"Could not check {file.name} for duplicates: {e}")

        return None

    def _apply_date_filters(
        self,
        df: pd.DataFrame,
        start_date: Optional[str],
        end_date: Optional[str]
    ) -> pd.DataFrame:
        """
        Apply date range filters to DataFrame

        Args:
            df: Input DataFrame
            start_date: Start date filter
            end_date: End date filter

        Returns:
            Filtered DataFrame
        """
        if start_date:
            start_dt = pd.to_datetime(start_date)
            df = df[df["timestamp"] >= start_dt]

        if end_date:
            end_dt = pd.to_datetime(end_date)
            df = df[df["timestamp"] <= end_dt]

        return df
