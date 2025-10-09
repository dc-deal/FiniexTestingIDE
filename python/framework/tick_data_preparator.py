"""
FiniexTestingIDE - Data Preparator
Prepares tick data for testing with warmup/test split
Uses timestamp-based loading for precise data requirements
"""

from python.components.logger.bootstrap_logger import setup_logging
from python.framework.utils.market_calendar import MarketCalendar
from python.framework.exceptions import InsufficientHistoricalDataError
from typing import Iterator, List, Tuple, Dict
from datetime import datetime, timedelta

import pandas as pd

from python.data_worker.data_loader.core import TickDataLoader
from python.framework.types import TickData, TimeframeConfig
from python.framework.utils.time_utils import format_duration

vLog = setup_logging(name="StrategyRunner")


class TickDataPreparator:
    """
    Prepares tick data for strategy testing.

    Uses timestamp-based loading instead of tick-count estimation
    for precise warmup data requirements.
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
        warmup_bar_requirements: Dict[str, int],
        test_start: datetime,
        test_end: datetime,
        max_test_ticks: int = None,
        data_mode: str = "realistic",
        scenario_name: str = None,
        config_path: str = None,
    ) -> Tuple[List[TickData], Iterator[TickData]]:
        """
        Prepare warmup and test data split using timestamp-based loading.

        Args:
            symbol: Trading symbol
            warmup_bar_requirements: Dict[timeframe, bars_needed] from workers
            test_start: When test period begins
            test_end: When test period ends
            max_test_ticks: Maximum number of test ticks (None = unlimited)
            data_mode: Data quality mode (clean/realistic/raw)
            scenario_name: Name of scenario (for error messages)
            config_path: Path to config file (for error messages)

        Returns:
            Tuple of (warmup_ticks, test_iterator)

        Raises:
            InsufficientHistoricalDataError: If data doesn't cover warmup period
            NotImplementedError: If timeframe requires weekend logic
        """
        # Convert bar requirements to time requirements
        warmup_minutes = self._convert_bars_to_minutes(warmup_bar_requirements)

        # Calculate when warmup must start
        max_minutes_needed = max(warmup_minutes.values())
        warmup_start = test_start - timedelta(minutes=max_minutes_needed)

        # Check for weekend overlap (informational)
        self._check_weekend_overlap(
            warmup_start, test_start, warmup_bar_requirements)

        vLog.info(f"📊 Preparing data for {symbol}")
        vLog.info(f"└─Warmup requirements: {warmup_minutes}")

        # Warmup period with readable duration
        warmup_duration = format_duration(warmup_start, test_start)
        vLog.info(
            f"└─Warmup period: {warmup_start.isoformat()} → {test_start.isoformat()} "
            f"({warmup_duration})"
        )

        # Test period with readable duration OR max ticks
        if max_test_ticks:
            vLog.info(
                f"└─Test period: {test_start.isoformat()} → {test_end.isoformat()} "
                f"(max {max_test_ticks:,} ticks)"
            )
        else:
            test_duration = format_duration(test_start, test_end)
            vLog.info(
                f"└─Test period: {test_start.isoformat()} → {test_end.isoformat()} "
                f"({test_duration})"
            )

        vLog.info(f"└─Data mode: {data_mode}")

        # Calculate and display test duration or tick limit
        if max_test_ticks:
            vLog.info(f"└─Max ticks: {max_test_ticks:,}")
        else:
            # Calculate duration in readable format
            duration = test_end - test_start
            total_seconds = int(duration.total_seconds())
            hours = total_seconds // 3600
            minutes = (total_seconds % 3600) // 60

            if hours > 0:
                vLog.info(f"└─Emulated Running Time Span: {hours}h {minutes}m")
            else:
                vLog.info(f"└─Emulated Running Time Span: {minutes}m")

        # NEW: Pass data_mode to loader for proper duplicate handling
        df = self.data_worker.load_symbol_data(
            symbol=symbol,
            start_date=warmup_start.isoformat(),
            end_date=test_end.isoformat(),
            use_cache=True,
            data_mode=data_mode  # NEW: Forward data_mode from scenario
        )

        if df.empty:
            raise ValueError(f"No data available for {symbol}")

        vLog.debug(f"✅ Loaded {len(df):,} ticks for {symbol}")

        # Validate that we have data covering warmup period
        # Convert and normalize ALL timestamps to timezone-naive for consistent comparison
        df['timestamp'] = pd.to_datetime(df['timestamp'])

        # Remove timezone info from pandas Series if present
        if df['timestamp'].dt.tz is not None:
            df['timestamp'] = df['timestamp'].dt.tz_localize(None)

        first_tick = df.iloc[0]['timestamp']
        last_tick = df.iloc[-1]['timestamp']

        # Normalize test_start and test_end to timezone-naive for comparison
        test_start_naive = test_start.replace(
            tzinfo=None) if test_start.tzinfo else test_start
        test_end_naive = test_end.replace(
            tzinfo=None) if test_end.tzinfo else test_end
        warmup_start_naive = warmup_start.replace(
            tzinfo=None) if warmup_start.tzinfo else warmup_start

        # Normalize first_tick (pandas Timestamp)
        first_tick_naive = first_tick.replace(tzinfo=None) if hasattr(
            first_tick, 'tzinfo') and first_tick.tzinfo else first_tick

        if first_tick_naive > warmup_start_naive:
            raise InsufficientHistoricalDataError(
                required_start=warmup_start_naive,
                first_available=first_tick_naive,
                symbol=symbol,
                scenario_name=scenario_name,
                scenario_start=test_start,
                warmup_duration_minutes=max_minutes_needed,
                config_path=config_path
            )

        # Split data at test_start timestamp
        # DataFrames are already tz-naive from above normalization
        warmup_df = df[df['timestamp'] < test_start_naive]
        test_df = df[(df['timestamp'] >= test_start_naive)
                     & (df['timestamp'] <= test_end_naive)]

        # Limit test_df to max_test_ticks if specified
        if max_test_ticks and len(test_df) > max_test_ticks:
            vLog.info(
                f"⚠️  Limiting test data: {len(test_df):,} → {max_test_ticks:,} ticks")
            test_df = test_df.iloc[:max_test_ticks]

        vLog.info(
            f"📦 Split: {len(warmup_df):,} warmup ticks, {len(test_df):,} test ticks"
        )

        # Validate we got test data
        if test_df.empty:
            raise ValueError(
                f"No test data found between {test_start} and {test_end}"
            )

        # Convert to TickData objects
        warmup_ticks = self._df_to_ticks(warmup_df, symbol)
        test_iterator = self._df_to_tick_iterator(test_df, symbol)

        return warmup_ticks, test_iterator

    def _convert_bars_to_minutes(
        self, bar_requirements: Dict[str, int]
    ) -> Dict[str, int]:
        """
        Convert bar requirements to minute requirements per timeframe.

        Args:
            bar_requirements: Dict[timeframe, bars_needed]

        Returns:
            Dict[timeframe, minutes_needed]

        Raises:
            NotImplementedError: If timeframe requires weekend logic
        """
        if not bar_requirements:
            raise ValueError("bar_requirements cannot be empty")

        minute_requirements = {}

        for timeframe, bars_needed in bar_requirements.items():
            # Validate timeframe doesn't require weekend logic
            MarketCalendar.validate_timeframe_for_weekends(timeframe)

            # Convert bars to minutes
            minutes_per_bar = TimeframeConfig.get_minutes(timeframe)
            minutes_needed = bars_needed * minutes_per_bar

            minute_requirements[timeframe] = minutes_needed

            vLog.debug(
                f"Timeframe {timeframe}: {bars_needed} bars × {minutes_per_bar} min/bar "
                f"= {minutes_needed} minutes"
            )

        return minute_requirements

    def _check_weekend_overlap(
        self,
        warmup_start: datetime,
        test_start: datetime,
        bar_requirements: Dict[str, int]
    ) -> None:
        """
        Check if warmup period spans weekend and log info.

        For intraday timeframes, weekend overlap is OK - we'll use Friday's data.
        For daily+ timeframes, this is blocked in validate_timeframe_for_weekends.

        Args:
            warmup_start: When warmup begins
            test_start: When test begins
            bar_requirements: Original bar requirements
        """
        spans_weekend, weekend_days = MarketCalendar.spans_weekend(
            warmup_start, test_start
        )

        if spans_weekend:
            vLog.info(
                f"ℹ️  Warmup period spans {weekend_days} weekend day(s)"
            )
            vLog.info(
                f"   → Will use available data from previous trading days (standard practice)"
            )

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
