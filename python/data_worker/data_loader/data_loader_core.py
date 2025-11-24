"""
FiniexTestingIDE Data Loader - Core Module

 Support für neue Collector-First Hierarchie
 mt5/ticks/EURUSD/*.parquet
"""

from datetime import timezone
from pathlib import Path
from typing import List, Optional

import pandas as pd
import pyarrow.parquet as pq

from python.components.logger.abstract_logger import AbstractLogger
from python.data_worker.data_loader.data_loader_exceptions import (
    ArtificialDuplicateException,
    DuplicateReport,
    InvalidDataModeException
)
from python.data_worker.data_loader.tick_index_manager import TickIndexManager
from python.framework.utils.market_calendar import MarketCalendar

from python.components.logger.bootstrap_logger import get_logger
vLog = get_logger()


class TickDataLoader:
    """
    Core tick data loading with caching and filtering.

    """

    VALID_DATA_MODES = ["raw", "realistic", "clean"]

    def __init__(self, data_dir: str = "./data/processed/"):
        self.data_dir = Path(data_dir)
        if not self.data_dir.exists():
            raise FileNotFoundError(f"Data directory not found: {data_dir}")

        self._symbol_cache = {}

        # Index manager bleibt unverändert - arbeitet transparent mit neuer Struktur
        self.index_manager = TickIndexManager(self.data_dir)
        self.index_manager.build_index()

    def list_available_symbols(self) -> List[str]:
        """List all available symbols [UNCHANGED - uses index]"""
        return self.index_manager.list_symbols()

    def load_symbol_data(
        self,
        symbol: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        data_mode: str = "realistic",
        use_cache: bool = True,
        detect_artificial_duplicates: bool = True,
        logger: AbstractLogger = vLog
    ) -> pd.DataFrame:
        """
        Load tick data for a symbol with optional date filtering.
        Index handles new structure transparently]
        """
        # Validate data mode
        if data_mode not in self.VALID_DATA_MODES:
            raise InvalidDataModeException(data_mode, self.VALID_DATA_MODES)

        # Cache key
        cache_key = f"{symbol}_{start_date}_{end_date}_{data_mode}"

        if use_cache and cache_key in self._symbol_cache:
            logger.debug(f"📦 Cache hit for {cache_key}")
            return self._symbol_cache[cache_key].copy()

        # Index-based file selection (works transparently with new structure)
        if start_date and end_date:
            start_dt = pd.to_datetime(start_date)
            end_dt = pd.to_datetime(end_date)

            files = self.index_manager.get_relevant_files(
                symbol, start_dt, end_dt)

            all_files_count = len(self.index_manager.index.get(symbol, []))

            if files:
                logger.info(
                    f"📊 Loading {len(files)}/{all_files_count} files for {symbol} "
                    f"({MarketCalendar.format_time_range(start_date, end_date)})"
                )
            else:
                logger.warning(f"No files found for {symbol} in date range")
                return pd.DataFrame()
        else:
            # No date filter: load all files
            files = self._get_symbol_files(symbol)
            logger.info(f"📊 Loading all {len(files)} files for {symbol}")

        if not files:
            logger.warning(f"No Parquet files found for {symbol}")
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
                logger.info(
                    f"🔁 Removed {duplicates_removed:,} natural duplicates ")
                logger.info(
                    f"from {initial_count:,} total ticks ({duplicate_percentage:.2f}% of data) [data_mode={data_mode}]"
                )
            else:
                logger.info(
                    f"🔁 No natural duplicates found in {initial_count:,} ticks")
        else:
            logger.info(
                f"🔁 Keeping all ticks including natural duplicates [data_mode={data_mode}]"
            )

        # Apply date filters
        combined_df = self._apply_date_filters(
            combined_df, start_date, end_date)

        # Update cache
        if use_cache:
            self._symbol_cache[cache_key] = combined_df.copy()

        logger.info(f"✅ Loaded: {len(combined_df):,} ticks for {symbol}")
        return combined_df

    # =========================================================================
    # EXISTING METHODS - ANGEPASST
    # =========================================================================

    def _get_symbol_files(self, symbol: str) -> List[Path]:
        """
        Get all Parquet files for a symbol (legacy method).

        CHANGED: Neue Glob-Pattern für Collector-First Hierarchie
        """
        # CHANGED: Neue Hierarchie berücksichtigen
        # Pattern: */ticks/SYMBOL/*.parquet
        pattern = f"*/ticks/{symbol}/{symbol}_*.parquet"
        files = list(self.data_dir.glob(pattern))
        return sorted(files)

    def _check_artificial_duplicates(self, files: List[Path], logger: AbstractLogger = vLog) -> Optional[DuplicateReport]:
        """Check for artificial duplicates via Parquet metadata """
        if not files:
            return None

        source_files = {}

        for file in files:
            try:
                pq_file = pq.ParquetFile(file)
                metadata_raw = pq_file.metadata.metadata

                source_file = metadata_raw.get(
                    b'source_file', b'').decode('utf-8')

                if source_file in source_files:
                    existing_file = source_files[source_file]

                    logger.error(
                        f"❌ ARTIFICIAL DUPLICATE DETECTED!")
                    logger.error(
                        f"   Source: {source_file}")
                    logger.error(
                        f"   File 1: {existing_file.name}")
                    logger.error(
                        f"   File 2: {file.name}")

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
                logger.error(
                    f"Could not check {file.name} for duplicates: {e}")
                raise

        return None

    def _apply_date_filters(
        self,
        df: pd.DataFrame,
        start_date: Optional[str],
        end_date: Optional[str]
    ) -> pd.DataFrame:
        """Apply date range filters to DataFrame """

        if df["timestamp"].dt.tz is None:
            df["timestamp"] = df["timestamp"].dt.tz_localize("UTC")

        if start_date:
            start_dt = pd.to_datetime(start_date, utc=True)
            df = df[df["timestamp"] >= start_dt]

        if end_date:
            end_dt = pd.to_datetime(end_date, utc=True)
            df = df[df["timestamp"] <= end_dt]

        return df
