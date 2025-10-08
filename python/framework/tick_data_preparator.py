"""
FiniexTestingIDE - Data Preparator
Prepares tick data for testing with warmup/test split
"""

from python.components.logger.bootstrap_logger import setup_logging
from typing import Iterator, List, Tuple, Dict

import pandas as pd

from python.data_worker.data_loader.core import TickDataLoader
from python.framework.types import TickData

vLog = setup_logging(name="StrategyRunner")


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
        warmup_requirements: Dict[str, int],
        test_ticks_count: int = 1000,
        data_mode: str = "realistic",
        start_date: str = None,
        end_date: str = None,
    ) -> Tuple[List[TickData], Iterator[TickData]]:
        """
        Prepare warmup and test data split

        Args:
            symbol: Trading symbol
            warmup_requirements: Dict[timeframe, minutes_needed] from workers
            test_ticks_count: Number of test ticks
            data_mode: Data quality mode (clean/realistic/raw)
            start_date: Optional start date
            end_date: Optional end date

        Returns:
            Tuple of (warmup_ticks, test_iterator)
        """
        # Calculate accurate tick estimation from timeframe requirements
        warmup_ticks_estimate = self._calculate_warmup_ticks_from_requirements(
            warmup_requirements
        )

        vLog.info(f"📊 Preparing data for {symbol}")
        vLog.info(f"└─Warmup requirements: {warmup_requirements}")
        vLog.info(f"└─Estimated warmup ticks: {warmup_ticks_estimate:,}")
        vLog.info(f"└─Test ticks: {test_ticks_count:,}")
        vLog.info(f"└─Data mode: {data_mode}")

        # Load data
        df = self.data_worker.load_symbol_data(
            symbol=symbol, start_date=start_date, end_date=end_date, use_cache=True
        )

        if df.empty:
            raise ValueError(f"No data available for {symbol}")

        vLog.debug(f"✅ Loaded {len(df):,} ticks for {symbol}")

        total_needed = warmup_ticks_estimate + test_ticks_count

        if len(df) < total_needed:
            vLog.warning(
                f"⚠️ Limited data: {len(df):,} < {total_needed:,} needed"
            )
            warmup_ticks_estimate = max(0, len(df) - test_ticks_count)

        # Split data
        warmup_df = df.iloc[:warmup_ticks_estimate]
        test_df = df.iloc[
            warmup_ticks_estimate: warmup_ticks_estimate + test_ticks_count
        ]

        vLog.info(
            f"📦 Split: {len(warmup_df):,} warmup ticks, {len(test_df):,} test ticks"
        )

        # Convert to TickData objects
        warmup_ticks = self._df_to_ticks(warmup_df, symbol)
        test_iterator = self._df_to_tick_iterator(test_df, symbol)

        return warmup_ticks, test_iterator

    def _calculate_warmup_ticks_from_requirements(
        self, warmup_requirements: Dict[str, int]
    ) -> int:
        """
        Calculate required warmup ticks based on timeframe requirements.

        Uses the maximum minutes needed across all timeframes and estimates
        ticks per minute based on typical Forex market activity.

        Args:
            warmup_requirements: Dict[timeframe, minutes_needed]

        Returns:
            Estimated number of ticks needed
        """
        if not warmup_requirements:
            raise ValueError("warmup_requirements cannot be empty")

        # Get maximum warmup time needed
        max_minutes_needed = max(warmup_requirements.values())

        # Estimate ticks per minute (adjust based on your market)
        # Forex typically has 30-80 ticks/minute, we use conservative estimate
        TICKS_PER_MINUTE = 50

        # Add 20% safety margin for incomplete bars
        estimated_ticks = int(max_minutes_needed * TICKS_PER_MINUTE * 1.2)

        vLog.debug(
            f"Warmup estimation: {max_minutes_needed} minutes × "
            f"{TICKS_PER_MINUTE} ticks/min × 1.2 safety = {estimated_ticks:,} ticks"
        )

        return estimated_ticks

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
