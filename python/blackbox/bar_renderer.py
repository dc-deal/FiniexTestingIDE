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
    
    def initialize_historical_bars(
    self, symbol: str, timeframe: str, bars: List[Bar]
    ) -> None:
        """
        Initialize the completed_bars history with warmup bars.
        
        This method populates the historical bar storage with pre-rendered
        warmup bars, making them available for get_bar_history() calls.
        
        Args:
            symbol: The trading symbol (e.g., "EURUSD")
            timeframe: The timeframe (e.g., "M5")
            bars: List of completed warmup bars to initialize with
        """
        # Ensure the nested structure exists for this timeframe/symbol
        if timeframe not in self.completed_bars:
            self.completed_bars[timeframe] = defaultdict(deque)
        
        if symbol not in self.completed_bars[timeframe]:
            self.completed_bars[timeframe][symbol] = deque(maxlen=1000)
        
        # Add all warmup bars to the history
        # These are already complete bars from the warmup phase
        for bar in bars:
            self.completed_bars[timeframe][symbol].append(bar)
        
        logger.debug(
            f"Initialized {len(bars)} historical {timeframe} bars for {symbol}"
        )




