import time
from python.components.logger.bootstrap_logger import get_logger
from collections import defaultdict, deque
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set

import pandas as pd

from python.framework.exceptions import InsufficientWarmupDataError
from python.framework.types import Bar, TickData, TimeframeConfig

vLog = get_logger()


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

        Raises:
            InsufficientWarmupDataError: If not enough bars could be rendered
        """
        historical_bars = {}

        vLog.debug(
            f"Preparing warmup from {len(warmup_ticks)} pre-loaded ticks"
        )

        # Render bars for each required timeframe
        for timeframe, minutes_needed in warmup_requirements.items():
            # Render ALL bars from ticks
            bars = self._render_bars_from_ticks(
                warmup_ticks, timeframe, symbol)

            # Calculate how many bars we need
            bars_needed = minutes_needed // TimeframeConfig.get_minutes(
                timeframe)

            # HARD VALIDATION: Do we have enough bars?
            if len(bars) < bars_needed:
                raise InsufficientWarmupDataError(
                    timeframe=timeframe,
                    required_bars=bars_needed,
                    rendered_bars=len(bars),
                    last_bar_timestamp=bars[-1].timestamp if bars else None
                )

            # Take only the last N bars needed for warmup
            historical_bars[timeframe] = bars[-bars_needed:]

            vLog.debug(
                f"✅ Prepared {len(historical_bars[timeframe])} {timeframe} bars "
                f"for warmup (required: {bars_needed}, rendered: {len(bars)})"
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

        # LOGGING: Start
        loop_start = time.perf_counter()
        timestamp_conversion_time = 0
        bar_start_time_calc = 0
        bar_comparison_time = 0

        for i, tick in enumerate(ticks):
            # Messe Timestamp-Konvertierung
            t1 = time.perf_counter()
            timestamp = pd.to_datetime(tick.timestamp)
            timestamp_conversion_time += time.perf_counter() - t1

            # Messe Bar-Start-Time Berechnung
            t2 = time.perf_counter()
            bar_start_time = TimeframeConfig.get_bar_start_time(
                timestamp, timeframe)
            bar_start_time_calc += time.perf_counter() - t2

            # Messe Bar-Vergleich
            t3 = time.perf_counter()
            needs_new_bar = (
                current_bar is None
                or pd.to_datetime(current_bar.timestamp) != bar_start_time
            )
            bar_comparison_time += time.perf_counter() - t3

            # verarbeitung
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

            if (i + 1) % 1000 == 0:
                vLog.debug(f"⏱️ Processed {i+1}/{len(ticks)} ticks - "
                           f"Timestamp conv: {timestamp_conversion_time*1000:.1f}ms, "
                           f"Bar calc: {bar_start_time_calc*1000:.1f}ms, "
                           f"Comparison: {bar_comparison_time*1000:.1f}ms")

        # Add final bar
        if current_bar is not None:
            current_bar.is_complete = True
            bars.append(current_bar)

        # FINAL LOGGING
        total_time = time.perf_counter() - loop_start
        vLog.info(f"🔍 Bar rendering breakdown for {len(ticks)} ticks:")
        vLog.info(f"   Total time: {total_time*1000:.1f}ms")
        vLog.info(
            f"   ├─ Timestamp conversions: {timestamp_conversion_time*1000:.1f}ms ({timestamp_conversion_time/total_time*100:.1f}%)")
        vLog.info(
            f"   ├─ Bar start calculations: {bar_start_time_calc*1000:.1f}ms ({bar_start_time_calc/total_time*100:.1f}%)")
        vLog.info(
            f"   ├─ Bar comparisons: {bar_comparison_time*1000:.1f}ms ({bar_comparison_time/total_time*100:.1f}%)")
        vLog.info(
            f"   └─ Other operations: {(total_time - timestamp_conversion_time - bar_start_time_calc - bar_comparison_time)*1000:.1f}ms")

        return bars
