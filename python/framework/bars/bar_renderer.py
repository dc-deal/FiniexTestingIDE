"""
FiniexTestingIDE - Bar Rendering System
Converts ticks to bars for all timeframes

PERFORMANCE OPTIMIZED:
- Removed pd.to_datetime() calls (timestamp is already datetime)
- Removed pd.to_datetime() comparison for bar timestamps
- Expected speedup: 50-70% reduction in bar rendering time
"""

from collections import defaultdict, deque
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple

from python.components.logger.scenario_logger import ScenarioLogger
from python.framework.types.market_data_types import Bar, TickData
from python.framework.types.timeframe_types import TimeframeConfig


class BarRenderer:
    """Core bar renderer for all timeframes"""

    def __init__(self, logger: ScenarioLogger):
        self.current_bars: Dict[str, Dict[str, Bar]] = defaultdict(
            dict
        )  # {timeframe: {symbol: bar}}
        self.completed_bars: Dict[str, Dict[str, deque]] = defaultdict(
            lambda: defaultdict(deque)
        )
        self._last_tick_time: Optional[datetime] = None
        self.logger = logger

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
    ) -> Tuple[Dict[str, Bar], Dict[str, bool]]:
        """
        Update bars for all timeframes with new tick.

        PERFORMANCE OPTIMIZED:
        - tick_data.timestamp is already datetime (no parsing needed)
        - Direct datetime comparison (no pd.to_datetime)
        - Cached bar_start_time calculation (in TimeframeConfig)

        Returns:
            Tuple of:
            - Dict[timeframe, Bar]: Updated current bars
            - Dict[timeframe, bool]: Which bars were closed this tick
        """
        symbol = tick_data.symbol
        timestamp = tick_data.timestamp  # Already datetime!
        mid_price = tick_data.mid
        volume = tick_data.volume

        updated_bars = {}
        closed_bars = {}

        for timeframe in required_timeframes:
            # Cached calculation - much faster for repeated calls
            bar_start_time = TimeframeConfig.get_bar_start_time(
                timestamp, timeframe)

            # Check if we need a new bar
            current_bar = self.current_bars[timeframe].get(symbol)

            bar_was_closed = False
            if current_bar is None:
                # First bar
                pass
            else:
                # Compare bar timestamps directly (both are datetime)
                current_bar_start = datetime.fromisoformat(
                    current_bar.timestamp)
                if current_bar_start != bar_start_time:
                    # Bar period changed - close old bar
                    current_bar.is_complete = True
                    self._archive_completed_bar(symbol, timeframe, current_bar)
                    bar_was_closed = True

            # Create new bar if needed
            if current_bar is None or bar_was_closed:
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

            # Check if bar is complete (time-based)
            if TimeframeConfig.is_bar_complete(bar_start_time, timestamp, timeframe):
                current_bar.is_complete = True

            updated_bars[timeframe] = current_bar
            closed_bars[timeframe] = bar_was_closed

        self._last_tick_time = timestamp
        return updated_bars, closed_bars

    def _archive_completed_bar(self, symbol: str, timeframe: str, bar: Bar):
        """Archive completed bar to history"""
        max_history = 1000

        # ============ DEBUG START ============
        self.logger.verbose(
            f"🔍 [BAR ARCHIVED] {timeframe} bar closed: {bar.timestamp}")
        # ============ DEBUG END ============

        self.logger.verbose(
            f"📊 {bar.symbol} {bar.timeframe} archived | "
            f"{bar.timestamp[:16]} | Close: {bar.close:.5f} | Ticks: {bar.tick_count}"
        )
        history = self.completed_bars[timeframe][symbol]

        history.append(bar)

        # ============ DEBUG START ============
        self.logger.verbose(f"   History size AFTER append: {len(history)}")
        # ============ DEBUG END ============

        if len(history) > max_history:
            history.popleft()

    def get_bar_history(
        self, symbol: str, timeframe: str
    ) -> List[Bar]:
        """
        Get historical bars.

        PERFORMANCE NOTE:
        This converts the internal deque to a list. To optimize performance,
        the BarRenderingController caches the result and only calls this
        when a bar closes, reducing calls from 2000+ to ~10-20 per test.

        Returns:
            List[Bar]: Historical bars
        """
        history = self.completed_bars[timeframe][symbol]
        return list(history)

    def get_current_bar(self, symbol: str, timeframe: str) -> Optional[Bar]:
        """Get current bar for symbol/timeframe"""
        return self.current_bars[timeframe].get(symbol)

    def initialize_historical_bars(
        self, symbol: str, timeframe: str, bars: Tuple[Bar, ...]
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
        for bar in bars:
            self.completed_bars[timeframe][symbol].append(bar)

        self.logger.debug(
            f"Initialized {len(bars)} historical {timeframe} bars for {symbol}"
        )

    def render_bars_from_ticks(
        self, ticks: List[TickData], symbol: str, timeframe: str
    ) -> List[Bar]:
        """
        Render a list of bars from a sequence of ticks.

        PERFORMANCE OPTIMIZED:
        - tick.timestamp is already datetime (no parsing)

        Args:
            ticks: List of TickData objects to process
            symbol: The trading symbol
            timeframe: The timeframe to render (e.g., "M5")

        Returns:
            List of completed Bar objects
        """
        bars = []
        current_bar = None

        for tick in ticks:
            timestamp = tick.timestamp  # Already datetime!
            mid_price = tick.mid
            volume = tick.volume

            bar_start_time = TimeframeConfig.get_bar_start_time(
                timestamp, timeframe)

            # Check if we need to start a new bar
            if current_bar is None:
                # First bar
                pass
            else:
                current_bar_start = datetime.fromisoformat(
                    current_bar.timestamp)
                if current_bar_start != bar_start_time:
                    # Complete and archive the previous bar
                    current_bar.is_complete = True
                    bars.append(current_bar)
                    current_bar = None

            # Create new bar if needed
            if current_bar is None:
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

            # Update the current bar with this tick
            current_bar.update_with_tick(mid_price, volume)

        # Don't forget to add the last bar if it exists
        if current_bar is not None:
            current_bar.is_complete = True
            bars.append(current_bar)

        return bars
