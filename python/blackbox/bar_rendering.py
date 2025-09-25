"""
FiniexTestingIDE - Bar Rendering System
Converts ticks to bars for all timeframes
"""

import logging
from typing import Dict, List, Set, Optional
from collections import defaultdict, deque
from datetime import datetime, timedelta
import pandas as pd

from python.blackbox.types import TickData, Bar, TimeframeConfig

logger = logging.getLogger(__name__)


class BarRenderer:
    """Core bar renderer for all timeframes"""

    def __init__(self):
        self.current_bars: Dict[str, Dict[str, Bar]] = defaultdict(
            dict
        )  # {timeframe: {symbol: bar}}
        self.completed_bars: Dict[str, Dict[str, deque]] = defaultdict(
            lambda: defaultdict(deque)
        )
        self._last_tick_time: Optional[datetime] = None

    def get_required_timeframes(self, workers) -> Set[str]:
        """Collect all required timeframes from workers"""
        timeframes = set()
        for worker in workers:
            if hasattr(worker, "required_timeframes"):
                timeframes.update(worker.required_timeframes)
            elif hasattr(worker, "timeframe"):
                timeframes.add(worker.timeframe)
        return timeframes or {"M1"}

    def update_current_bars(
        self, tick_data: TickData, required_timeframes: Set[str]
    ) -> Dict[str, Bar]:
        """
        Update bars for all timeframes with new tick

        Returns:
            Dict[timeframe, Bar] - Updated current bars
        """
        symbol = tick_data.symbol
        timestamp = pd.to_datetime(tick_data.timestamp)
        mid_price = tick_data.mid
        volume = tick_data.volume

        updated_bars = {}

        for timeframe in required_timeframes:
            bar_start_time = TimeframeConfig.get_bar_start_time(timestamp, timeframe)

            # Check if we need a new bar
            current_bar = self.current_bars[timeframe].get(symbol)

            if (
                current_bar is None
                or pd.to_datetime(current_bar.timestamp) != bar_start_time
            ):

                # Complete old bar if exists
                if current_bar is not None:
                    current_bar.is_complete = True
                    self._archive_completed_bar(symbol, timeframe, current_bar)

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
                self.current_bars[timeframe][symbol] = current_bar

            # Update bar with tick
            current_bar.update_with_tick(mid_price, volume)

            # Check if bar is complete
            if TimeframeConfig.is_bar_complete(bar_start_time, timestamp, timeframe):
                current_bar.is_complete = True

            updated_bars[timeframe] = current_bar

        self._last_tick_time = timestamp
        return updated_bars

    def _archive_completed_bar(self, symbol: str, timeframe: str, bar: Bar):
        """Archive completed bar to history"""
        max_history = 1000
        history = self.completed_bars[timeframe][symbol]

        history.append(bar)
        if len(history) > max_history:
            history.popleft()

    def get_bar_history(
        self, symbol: str, timeframe: str, count: int = 50
    ) -> List[Bar]:
        """Get historical bars"""
        history = self.completed_bars[timeframe][symbol]
        return list(history)[-count:] if len(history) >= count else list(history)

    def get_current_bar(self, symbol: str, timeframe: str) -> Optional[Bar]:
        """Get current bar for symbol/timeframe"""
        return self.current_bars[timeframe].get(symbol)


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


class BarRenderingOrchestrator:
    """Main orchestrator for bar rendering system"""

    def __init__(self, data_loader):
        self.bar_renderer = BarRenderer()
        self.warmup_manager = WarmupManager(data_loader)
        self._workers = []
        self._required_timeframes = set()
        self._warmup_data = {}

    def register_workers(self, workers):
        """Register workers and analyze requirements"""
        self._workers = workers
        self._required_timeframes = self.bar_renderer.get_required_timeframes(workers)

        logger.info(
            f"Registered {len(workers)} workers requiring timeframes: {self._required_timeframes}"
        )

    def prepare_warmup(self, symbol: str, test_start_time: datetime):
        """Prepare warmup data for all workers"""
        warmup_requirements = self.warmup_manager.calculate_required_warmup(
            self._workers
        )

        self._warmup_data = self.warmup_manager.prepare_historical_bars(
            symbol=symbol,
            test_start_time=test_start_time,
            warmup_requirements=warmup_requirements,
        )

        logger.info(
            f"Warmup prepared with {sum(len(bars) for bars in self._warmup_data.values())} total bars"
        )

    def process_tick(self, tick_data: TickData) -> Dict[str, Bar]:
        """Process tick and update all current bars"""
        return self.bar_renderer.update_current_bars(
            tick_data, self._required_timeframes
        )

    def get_warmup_bars(self, timeframe: str) -> List[Bar]:
        """Get warmup bars for specific timeframe"""
        return self._warmup_data.get(timeframe, [])

    def get_bar_history(
        self, symbol: str, timeframe: str, count: int = 50
    ) -> List[Bar]:
        """Get bar history (completed bars)"""
        return self.bar_renderer.get_bar_history(symbol, timeframe, count)

    def get_current_bar(self, symbol: str, timeframe: str) -> Optional[Bar]:
        """Get current bar"""
        return self.bar_renderer.get_current_bar(symbol, timeframe)
