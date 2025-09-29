import logging
from typing import Dict, List, Set, Optional
from collections import defaultdict, deque
from datetime import datetime, timedelta
import pandas as pd

from python.blackbox.types import TickData, Bar, TimeframeConfig

logger = logging.getLogger(__name__)



class WarmupManager:
    """Manages historical data warmup for workers"""

    def __init__(self, data_loader):
        self.data_loader = data_loader

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
        warmup_start_time = test_start_time - timedelta(minutes=max_warmup_minutes)

        logger.info(f"Preparing warmup from {warmup_start_time} to {test_start_time}")

        # Load tick data for warmup period
        tick_data = self.data_loader.load_symbol_data(
            symbol=symbol,
            start_date=warmup_start_time.isoformat(),
            end_date=test_start_time.isoformat(),
        )

        if tick_data.empty:
            logger.warning(f"No warmup data found for {symbol}")
            return {}

        # Render historical bars for each timeframe
        for timeframe, minutes_needed in warmup_requirements.items():
            bars = self._render_historical_bars(tick_data, timeframe, symbol)
            historical_bars[timeframe] = (
                bars[-minutes_needed // TimeframeConfig.get_minutes(timeframe) :]
                if bars
                else []
            )

            logger.info(
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

            bar_start_time = TimeframeConfig.get_bar_start_time(timestamp, timeframe)

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