"""
FiniexTestingIDE Data Loader - Core Module
Pure loading logic: Fast, focused, zero dependencies on analysis

Location: python/data_loader/core.py
"""

import pandas as pd
from pathlib import Path
from typing import List, Optional
import logging

logger = logging.getLogger(__name__)


class TickDataLoader:
    """
    Core tick data loading with caching and filtering

    Responsibilities:
    - Load parquet files from disk
    - Cache loaded data for performance
    - Apply date range filters
    - Remove duplicates

    Design: Minimal, fast, no analysis logic
    """

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

    def list_available_symbols(self) -> List[str]:
        """
        List all available symbols in data directory

        Returns:
            Sorted list of symbol names (e.g. ['EURUSD', 'GBPUSD', 'USDJPY'])
        """
        parquet_files = list(self.data_dir.glob("*.parquet"))
        symbols = set()

        for file in parquet_files:
            try:
                # Extract symbol from filename (FORMAT: SYMBOL_YYYYMMDD_HHMMSS.parquet)
                symbol = file.name.split("_")[0]
                symbols.add(symbol)
            except IndexError:
                logger.warning(f"Unexpected filename format: {file.name}")

        return sorted(list(symbols))

    def load_symbol_data(
        self,
        symbol: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        use_cache: bool = True,
    ) -> pd.DataFrame:
        """
        Load tick data for a symbol with optional date filtering

        Args:
            symbol: Currency pair (e.g. 'EURUSD')
            start_date: Start date (ISO format: '2024-01-15') or None for all data
            end_date: End date (ISO format: '2024-01-16') or None for all data
            use_cache: Whether to use cached data for performance

        Returns:
            DataFrame with columns: timestamp, bid, ask, volume, spread_points, etc.

        Raises:
            ValueError: If no data found for symbol
        """
        cache_key = f"{symbol}_{start_date}_{end_date}"

        # Check cache
        if use_cache and cache_key in self._symbol_cache:
            logger.info(f"Using cached data for {symbol}")
            return self._symbol_cache[cache_key].copy()

        # Find files for symbol
        files = self._get_symbol_files(symbol)

        if not files:
            raise ValueError(f"No data found for symbol {symbol}")

        logger.info(f"Loading {len(files)} files for {symbol}")

        # Load and combine all files
        dataframes = []
        for file in files:
            try:
                df = pd.read_parquet(file)
                dataframes.append(df)
            except Exception as e:
                logger.warning(f"Error reading {file}: {e}")

        if not dataframes:
            raise ValueError(f"No valid data found for {symbol}")

        # Combine and sort
        combined_df = pd.concat(dataframes, ignore_index=True)
        combined_df = combined_df.sort_values("timestamp").reset_index(drop=True)

        # Remove duplicates (keep latest)
        initial_count = len(combined_df)
        combined_df = combined_df.drop_duplicates(subset=["timestamp"], keep="last")

        if len(combined_df) < initial_count:
            logger.info(f"Removed {initial_count - len(combined_df)} duplicates")

        # Apply date filters
        combined_df = self._apply_date_filters(combined_df, start_date, end_date)

        # Update cache
        if use_cache:
            self._symbol_cache[cache_key] = combined_df.copy()

        logger.info(f"✓ Loaded: {len(combined_df):,} ticks for {symbol}")
        return combined_df

    def _apply_date_filters(
        self, df: pd.DataFrame, start_date: Optional[str], end_date: Optional[str]
    ) -> pd.DataFrame:
        """Apply date range filters to DataFrame"""
        if start_date:
            start_dt = pd.to_datetime(start_date).tz_localize("UTC")
            count_before = len(df)
            df = df[df["timestamp"] >= start_dt]
            count_after = len(df)

            logger.info(
                f"Start date filter: {count_before:,} -> {count_after:,} ticks "
                f"({count_after/count_before*100:.1f}% retained)"
            )

        if end_date:
            end_dt = pd.to_datetime(end_date).tz_localize("UTC")
            count_before = len(df)
            df = df[df["timestamp"] <= end_dt]
            count_after = len(df)

            logger.info(
                f"End date filter: {count_before:,} -> {count_after:,} ticks "
                f"({count_after/count_before*100:.1f}% retained)"
            )

        if len(df) == 0:
            logger.warning(f"⚠️ No data remaining after filtering!")
            logger.warning(f"Requested range: {start_date} to {end_date}")

        return df

    def _get_symbol_files(self, symbol: str) -> List[Path]:
        """Find all parquet files for a symbol"""
        pattern = f"{symbol}_*.parquet"
        files = list(self.data_dir.glob(pattern))
        return sorted(files)

    def clear_cache(self):
        """Clear the data cache to free memory"""
        self._symbol_cache.clear()
        logger.info("Data cache cleared")
