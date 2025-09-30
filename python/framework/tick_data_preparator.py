"""
FiniexTestingIDE - Data Preparator
Prepares tick data for testing with warmup/test split
"""

import logging
from typing import List, Tuple, Iterator
import pandas as pd

from python.framework.types import TickData
from python.data_worker.data_loader.core import TickDataLoader

logger = logging.getLogger(__name__)


class TickDataPreparator:
    """
    Prepares tick data for strategy testing
    Handles warmup/test splits and data quality
    """

    def __init__(self, data_worker: TickDataLoader):
        """
        Initialize preparator

        Args:
            data_worker: TickDataLoader instance
        """
        self.data_worker = data_worker

    def get_symbol_info(self, symbol: str) -> dict:
        """Get symbol information"""
        return self.data_worker.get_symbol_info(symbol)

    def prepare_test_and_warmup_split(
        self,
        symbol: str,
        warmup_bars_needed: int,
        test_ticks_count: int = 1000,
        data_mode: str = "realistic",
        start_date: str = None,
        end_date: str = None,
    ) -> Tuple[List[TickData], Iterator[TickData]]:
        """
        Prepare warmup and test data split

        Args:
            symbol: Trading symbol
            warmup_bars_needed: Number of warmup bars needed
            test_ticks_count: Number of test ticks
            data_mode: Data quality mode (clean/realistic/raw)
            start_date: Optional start date
            end_date: Optional end date

        Returns:
            Tuple of (warmup_ticks, test_iterator)
        """
        logger.info(f"📊 Preparing data for {symbol}")
        logger.info(f"  Warmup bars: {warmup_bars_needed}")
        logger.info(f"  Test ticks: {test_ticks_count}")
        logger.info(f"  Data mode: {data_mode}")

        # Load data
        df = self.data_worker.load_symbol_data(
            symbol=symbol, start_date=start_date, end_date=end_date, use_cache=True
        )

        if df.empty:
            raise ValueError(f"No data available for {symbol}")

        logger.info(f"✅ Loaded {len(df):,} ticks for {symbol}")

        # Estimate warmup ticks needed (rough: 1 bar = ~50 ticks for M1)
        warmup_ticks_estimate = warmup_bars_needed * 50
        total_needed = warmup_ticks_estimate + test_ticks_count

        if len(df) < total_needed:
            logger.warning(f"⚠️ Limited data: {len(df)} < {total_needed} needed")
            warmup_ticks_estimate = max(0, len(df) - test_ticks_count)

        # Split data
        warmup_df = df.iloc[:warmup_ticks_estimate]
        test_df = df.iloc[
            warmup_ticks_estimate : warmup_ticks_estimate + test_ticks_count
        ]

        logger.info(
            f"📦 Split: {len(warmup_df)} warmup ticks, {len(test_df)} test ticks"
        )

        # Convert to TickData objects
        warmup_ticks = self._df_to_ticks(warmup_df, symbol)
        test_iterator = self._df_to_tick_iterator(test_df, symbol)

        return warmup_ticks, test_iterator

    def _df_to_ticks(self, df: pd.DataFrame, symbol) -> List[TickData]:
        """Convert DataFrame to list of TickData objects"""
        ticks = []

        for _, row in df.iterrows():
            tick = TickData(
                timestamp=(
                    row["timestamp"].isoformat()
                    if hasattr(row["timestamp"], "isoformat")
                    else str(row["timestamp"])
                ),
                symbol=symbol,
                bid=float(row["bid"]),
                ask=float(row["ask"]),
                volume=float(row.get("volume", 0)),
            )
            ticks.append(tick)

        return ticks

    def _df_to_tick_iterator(self, df: pd.DataFrame, symbol) -> Iterator[TickData]:
        """Convert DataFrame to tick iterator (memory efficient)"""
        for _, row in df.iterrows():
            yield TickData(
                timestamp=(
                    row["timestamp"].isoformat()
                    if hasattr(row["timestamp"], "isoformat")
                    else str(row["timestamp"])
                ),
                symbol=symbol,
                bid=float(row["bid"]),
                ask=float(row["ask"]),
                volume=float(row.get("volume", 0)),
            )
