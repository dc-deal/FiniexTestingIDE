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
    ) -> Iterator[TickData]:
        """
            Prepare test data iterator.

            UPDATED (Bar Pre-Rendering):
            - No warmup ticks needed (bars loaded from parquet)
            - Only loads test period ticks
            - warmup_bar_requirements only used for gap validation

            Args:
                symbol: Trading symbol
                warmup_bar_requirements: Dict[timeframe, bars_needed] (for gap threshold only)
                test_start: When test period begins
                test_end: When test period ends (ignored if max_test_ticks set)
                max_test_ticks: Maximum number of test ticks (None = timespan mode)
                data_mode: Data quality mode (clean/realistic/raw)
                scenario_name: Name of scenario (for error messages)
                config_path: Path to config file (for error messages)

            Returns:
                Iterator[TickData] - Test tick iterator

            Raises:
                InsufficientHistoricalDataError: If no ticks at test_start
                ValueError: If insufficient ticks or critical gaps
            """
        vLog.info(f"📊 Preparing data for {symbol}")

        # === MODE DETECTION ===
        if max_test_ticks:
            vLog.info(f"└─Mode: Tick-limited ({max_test_ticks:,} ticks)")
            vLog.info(
                f"└─Start: {test_start.strftime('%Y-%m-%d %H:%M:%S')}")
        else:
            duration = test_end - test_start
            hours = int(duration.total_seconds() // 3600)
            minutes = int((duration.total_seconds() % 3600) // 60)
            vLog.info(f"└─Mode: Timespan ({hours}h {minutes}m)")
            vLog.info(
                f"└─Period: {test_start.strftime('%Y-%m-%d %H:%M:%S')} → {test_end.strftime('%Y-%m-%d %H:%M:%S')}")

        vLog.info(f"└─Data mode: {data_mode}")

        # === DETERMINE LOAD WINDOW ===
        if max_test_ticks:
            # Load far enough to get required ticks (technical maximum)
            load_end = test_start + timedelta(days=365)
        else:
            load_end = test_end

        # === LOAD DATA ===
        df = self.data_worker.load_symbol_data(
            symbol=symbol,
            start_date=test_start.isoformat(),  # ← CHANGED: Start from test_start!
            end_date=load_end.isoformat(),
            use_cache=True,
            data_mode=data_mode
        )

        if df.empty:
            raise ValueError(
                f"No data available for {symbol} from {test_start}")

        vLog.debug(f"✅ Loaded {len(df):,} ticks for {symbol}")

        # === NORMALIZE TIMESTAMPS ===
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        if df['timestamp'].dt.tz is not None:
            df['timestamp'] = df['timestamp'].dt.tz_localize(None)

        first_tick = df.iloc[0]['timestamp']

        test_start_naive = test_start.replace(
            tzinfo=None) if test_start.tzinfo else test_start
        test_end_naive = test_end.replace(
            tzinfo=None) if test_end.tzinfo else test_end
        first_tick_naive = first_tick.replace(tzinfo=None) if hasattr(
            first_tick, 'tzinfo') and first_tick.tzinfo else first_tick

        # === VALIDATE TEST START COVERAGE ===
        if first_tick_naive > test_start_naive:
            raise InsufficientHistoricalDataError(
                required_start=test_start_naive,
                first_available=first_tick_naive,
                symbol=symbol,
                scenario_name=scenario_name,
                scenario_start=test_start,
                config_path=config_path
            )

        # === DISPATCH TO MODE-SPECIFIC VALIDATION ===
        if max_test_ticks:
            # Modus A: Tick-limited mode
            test_iterator = self._validate_and_prepare_tick_mode(
                df=df,
                symbol=symbol,
                test_start=test_start_naive,
                max_test_ticks=max_test_ticks,
                scenario_name=scenario_name
            )
        else:
            # Modus B: Timespan mode
            test_iterator = self._validate_and_prepare_timespan_mode(
                df=df,
                symbol=symbol,
                test_start=test_start_naive,
                test_end=test_end_naive,
                warmup_bar_requirements=warmup_bar_requirements,
                scenario_name=scenario_name
            )

        return test_iterator

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

    def _validate_and_prepare_tick_mode(
        self,
        df: pd.DataFrame,
        symbol: str,
        test_start: datetime,
        max_test_ticks: int,
        scenario_name: str
    ) -> Iterator[TickData]:
        """
        Modus A: Tick-limited mode validation and preparation.

        Takes first N ticks from test_start, validates count availability.

        Args:
            df: Full DataFrame (timezone-naive)
            symbol: Trading symbol
            test_start: Test start timestamp (timezone-naive)
            max_test_ticks: Required tick count
            scenario_name: Scenario name for error messages

        Returns:
            Tick iterator with exactly max_test_ticks (or less if insufficient)

        Raises:
            ValueError: If insufficient ticks available
        """
        # Get all ticks from test_start onwards
        test_df = df[df['timestamp'] >= test_start]

        # Take first N ticks
        test_df = test_df.iloc[:max_test_ticks]

        # Validate we got enough ticks
        if len(test_df) < max_test_ticks:
            raise ValueError(
                f"❌ Scenario '{scenario_name}': Insufficient tick data!\n"
                f"   Required: {max_test_ticks:,} ticks\n"
                f"   Available: {len(test_df):,} ticks from {test_start}\n"
                f"   Symbol: {symbol}"
            )

        vLog.info(f"✅ Tick-limited mode: {len(test_df):,} ticks ready")

        # Convert to iterator
        return self._df_to_tick_iterator(test_df, symbol)

    def _validate_and_prepare_timespan_mode(
        self,
        df: pd.DataFrame,
        symbol: str,
        test_start: datetime,
        test_end: datetime,
        warmup_bar_requirements: Dict[str, int],
        scenario_name: str
    ) -> Iterator[TickData]:
        """
        Modus B: Timespan mode validation and preparation.

        Validates tick availability in timespan and checks for critical gaps.

        Args:
            df: Full DataFrame (timezone-naive)
            symbol: Trading symbol
            test_start: Test start timestamp (timezone-naive)
            test_end: Test end timestamp (timezone-naive)
            warmup_bar_requirements: Warmup requirements (for gap threshold)
            scenario_name: Scenario name for error messages

        Returns:
            Tick iterator with all ticks in timespan

        Raises:
            ValueError: If no ticks in timespan or critical gaps detected
        """
        # Filter to test timespan
        test_df = df[(df['timestamp'] >= test_start)
                     & (df['timestamp'] <= test_end)]

        # Validate we have ticks
        if test_df.empty:
            raise ValueError(
                f"❌ Scenario '{scenario_name}': No ticks in test period!\n"
                f"   Timespan: {test_start} → {test_end}\n"
                f"   Symbol: {symbol}"
            )

        # === GAP VALIDATION ===
        # Get smallest timeframe requirement (defines gap tolerance)
        smallest_tf = min(
            warmup_bar_requirements.keys(),
            key=lambda tf: TimeframeConfig.get_minutes(tf)
        )
        min_bar_seconds = TimeframeConfig.get_minutes(smallest_tf) * 60

        # Get coverage report for gap analysis
        coverage = self.data_worker.index_manager.get_coverage_report(symbol)

        # Filter critical gaps in test period
        critical_gaps = [
            gap for gap in coverage.gaps
            if gap.gap_seconds > min_bar_seconds  # Größer als kleinster Bar
            and gap.gap_seconds > 60  # Ignore gaps < 1 minute
            # Im Test-Zeitraum
            and gap.file1.end_time.replace(tzinfo=None) >= test_start
            and gap.file2.start_time.replace(tzinfo=None) <= test_end
        ]

        if critical_gaps:
            # Build error message
            gap_details = "\n".join([
                f"   • {gap.duration_human} gap: "
                f"{gap.file1.end_time.strftime('%Y-%m-%d %H:%M')} → "
                f"{gap.file2.start_time.strftime('%H:%M')}"
                for gap in critical_gaps[:5]  # Show first 5
            ])

            raise ValueError(
                f"❌ Scenario '{scenario_name}': Critical gaps in test period!\n"
                f"   Symbol: {symbol}\n"
                f"   Timespan: {test_start} → {test_end}\n"
                f"   Smallest timeframe: {smallest_tf} ({min_bar_seconds}s bars)\n"
                f"\n"
                f"   GAPS > {min_bar_seconds}s found:\n"
                f"{gap_details}\n"
                f"\n"
                f"   → Bar rendering will be corrupted by these gaps!"
            )

        vLog.info(
            f"✅ Timespan mode: {len(test_df):,} ticks in period, no critical gaps")

        # Convert to iterator
        return self._df_to_tick_iterator(test_df, symbol)
