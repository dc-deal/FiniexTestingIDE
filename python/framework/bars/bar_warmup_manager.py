from python.components.logger.bootstrap_logger import setup_logging
from collections import defaultdict, deque
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set

import pandas as pd

from python.framework.types import Bar, TickData, TimeframeConfig

vLog = setup_logging(name="StrategyRunner")


class BarWarmupManager:
    """Manages historical data warmup for workers"""

    def __init__(self, data_worker):
        self.data_worker = data_worker

    def calculate_required_warmup(self, workers) -> Dict[str, int]:
        """Calculate maximum warmup time per timeframe"""
        warmup_requirements = defaultdict(int)

        for worker in workers:
            if hasattr(worker, "get_warmup_requirements"):
                requirements = worker.get_warmup_requirements()
                for timeframe, bars_needed in requirements.items():
                    minutes_needed = (
                        TimeframeConfig.get_minutes(timeframe) * bars_needed
                    )
                    warmup_requirements[timeframe] = max(
                        warmup_requirements[timeframe], minutes_needed
                    )

        return dict(warmup_requirements)

    def prepare_historical_bars(
        self,
        symbol: str,
        test_start_time: datetime,
        warmup_requirements: Dict[str, int],
    ) -> Dict[str, List[Bar]]:
        """Prepare historical bars for warmup"""
        historical_bars = {}

        # Calculate earliest required time
        max_warmup_minutes = (
            max(warmup_requirements.values()) if warmup_requirements else 60
        )
        warmup_start_time = test_start_time - \
            timedelta(minutes=max_warmup_minutes)

        vLog.info(
            f"Preparing warmup from {warmup_start_time} to {test_start_time}")

        # Load tick data for warmup period
        tick_data = self.data_worker.load_symbol_data(
            symbol=symbol,
            start_date=warmup_start_time.isoformat(),
            end_date=test_start_time.isoformat(),
        )

        if tick_data.empty:
            vLog.warning(f"No warmup data found for {symbol}")
            return {}

        # Render historical bars for each timeframe
        for timeframe, minutes_needed in warmup_requirements.items():
            bars = self._render_historical_bars(tick_data, timeframe, symbol)
            historical_bars[timeframe] = (
                bars[-minutes_needed //
                     TimeframeConfig.get_minutes(timeframe):]
                if bars
                else []
            )

            vLog.debug(
                f"Prepared {len(historical_bars[timeframe])} {timeframe} bars for warmup"
            )

        return historical_bars

    def _render_historical_bars(
        self, tick_data: pd.DataFrame, timeframe: str, symbol: str
    ) -> List[Bar]:
        """Render historical bars from tick data"""
        bars = []
        current_bar = None

        for _, tick in tick_data.iterrows():
            timestamp = pd.to_datetime(tick["timestamp"])
            mid_price = (tick["bid"] + tick["ask"]) / 2.0
            volume = tick.get("volume", 0)

            bar_start_time = TimeframeConfig.get_bar_start_time(
                timestamp, timeframe)

            if (
                current_bar is None
                or pd.to_datetime(current_bar.timestamp) != bar_start_time
            ):
                if current_bar is not None:
                    current_bar.is_complete = True
                    bars.append(current_bar)

                current_bar = Bar(
                    symbol=symbol,
                    timeframe=timeframe,
                    timestamp=bar_start_time.isoformat(),
                    open=0,
                    high=0,
                    low=0,
                    close=0,
                    volume=0,
                )

            current_bar.update_with_tick(mid_price, volume)

        if current_bar is not None:
            current_bar.is_complete = True
            bars.append(current_bar)

        return bars

    def prepare_warmup_from_ticks(
        self,
        symbol: str,
        warmup_ticks: List[TickData],
        warmup_requirements: Dict[str, int],
    ) -> Dict[str, List[Bar]]:
        """
        Prepare historical bars directly from already-loaded warmup ticks.

        This method avoids redundant data loading by working with ticks that
        have already been loaded by the TickDataPreparator. It renders bars
        for each required timeframe from the provided tick list.

        Args:
            symbol: Trading symbol (e.g., "EURUSD")
            warmup_ticks: List of TickData objects (already loaded)
            warmup_requirements: Dict[timeframe, minutes_needed]

        Returns:
            Dict[timeframe, List[Bar]] - Historical bars per timeframe
        """
        historical_bars = {}

        vLog.debug(
            f"Preparing warmup from {len(warmup_ticks)} pre-loaded ticks"
        )

        # Render bars for each required timeframe
        for timeframe, minutes_needed in warmup_requirements.items():
            # Use the bar_renderer's render logic (we'll need to import it)
            # For now, we'll use the local rendering method
            bars = self._render_bars_from_ticks(
                warmup_ticks, timeframe, symbol)

            # Take only the last N bars needed for warmup
            bars_needed = minutes_needed // TimeframeConfig.get_minutes(
                timeframe)
            historical_bars[timeframe] = bars[-bars_needed:] if bars else []

            vLog.debug(
                f"Prepared {len(historical_bars[timeframe])} {timeframe} bars for warmup"
            )

        return historical_bars

    def _render_bars_from_ticks(
        self, ticks: List[TickData], timeframe: str, symbol: str
    ) -> List[Bar]:
        """
        Internal method to render bars from a tick list.

        This duplicates some logic from the old _render_historical_bars method
        but works with TickData objects instead of a DataFrame.

        Args:
            ticks: List of TickData objects
            timeframe: Timeframe to render (e.g., "M5")
            symbol: Trading symbol

        Returns:
            List of completed Bar objects
        """
        bars = []
        current_bar = None

        for tick in ticks:
            timestamp = pd.to_datetime(tick.timestamp)
            mid_price = tick.mid
            volume = tick.volume

            bar_start_time = TimeframeConfig.get_bar_start_time(
                timestamp, timeframe)

            # Check if we need a new bar
            if (
                current_bar is None
                or pd.to_datetime(current_bar.timestamp) != bar_start_time
            ):
                # Complete previous bar
                if current_bar is not None:
                    current_bar.is_complete = True
                    bars.append(current_bar)

                # Create new bar
                current_bar = Bar(
                    symbol=symbol,
                    timeframe=timeframe,
                    timestamp=bar_start_time.isoformat(),
                    open=0,
                    high=0,
                    low=0,
                    close=0,
                    volume=0,
                )

            # Update bar with tick
            current_bar.update_with_tick(mid_price, volume)

        # Add final bar
        if current_bar is not None:
            current_bar.is_complete = True
            bars.append(current_bar)

        return bars
