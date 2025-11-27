"""
FiniexTestingIDE - Bar Rendering System
Converts ticks to bars for all timeframes

PERFORMANCE OPTIMIZED:
- Removed pd.to_datetime() calls (timestamp is already datetime)
- Removed pd.to_datetime() comparison for bar timestamps
- deque(maxlen) auto-trims for O(1) performance (no manual checks)
- Expected speedup: 50-70% reduction in bar rendering time
"""

from collections import defaultdict, deque
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set, Tuple

from python.components.logger.scenario_logger import ScenarioLogger
from python.framework.types.market_data_types import Bar, TickData
from python.framework.utils.timeframe_config_utils import TimeframeConfig


class BarRenderer:
    """Core bar renderer for all timeframes"""

    def __init__(self, logger: ScenarioLogger, max_history: int = 1000):
        """
        Initialize bar renderer.

        Args:
            logger: ScenarioLogger for logging
            max_history: Maximum bars to keep per symbol/timeframe

        PERFORMANCE LIMIT (not guessing!):
        max_history=1000 is a deliberate memory/performance trade-off:
        - Most indicators need <100 bars warmup
        - 1000 bars = ~16 hours (M1) or ~20 days (M30)
        - Prevents unbounded memory growth in long backtests
        - deque(maxlen) auto-trims with O(1) performance

        Post-MVP: Calculate dynamically from worker requirements.
        """
        self.max_history = max_history

        # Cache for bar start times: (timestamp_minute_key, timeframe) -> bar_start_time
        self._bar_start_cache: Dict[Tuple[str, str], datetime] = {}

        self.current_bars: Dict[str, Dict[str, Bar]] = defaultdict(
            dict
        )  # {timeframe: {symbol: bar}}

        # PERFORMANCE: deque(maxlen) auto-trims - no manual checks needed!
        self.completed_bars: Dict[str, Dict[str, deque]] = defaultdict(
            lambda: defaultdict(lambda: deque(maxlen=self.max_history))
        )

        self._last_tick_time: Optional[datetime] = None
        self.logger = logger

    def get_required_timeframes(self, workers) -> Set[str]:
        """Collect all required timeframes from workers"""
        timeframes = set()
        for worker in workers:
            timeframes.update(worker.get_required_timeframes())
        return timeframes

    def get_bar_start_time(self, timestamp: datetime, timeframe: str) -> datetime:
        """
        Calculate bar start time for given timestamp.

        PERFORMANCE OPTIMIZED:
        - Results are cached per minute
        - Same minute + timeframe always returns cached result
        - Reduces 40,000 calculations to ~few hundred cache lookups
        """
        # Create cache key from minute precision (ignore seconds/microseconds)
        cache_key = (
            f"{timestamp.year}-{timestamp.month:02d}-{timestamp.day:02d}"
            f"T{timestamp.hour:02d}:{timestamp.minute:02d}",
            timeframe
        )

        # Check cache first
        if cache_key in self._bar_start_cache:
            return self._bar_start_cache[cache_key]

        # Calculate bar start time
        minutes = TimeframeConfig.get_minutes(timeframe)
        total_minutes = timestamp.hour * 60 + timestamp.minute
        bar_start_minute = (total_minutes // minutes) * minutes

        bar_start = timestamp.replace(
            minute=bar_start_minute % 60,
            hour=bar_start_minute // 60,
            second=0,
            microsecond=0,
        )

        # Cache result
        self._bar_start_cache[cache_key] = bar_start

        # Limit cache size (keep last 10,000 entries)
        if len(self._bar_start_cache) > 10000:
            # Remove oldest 5000 entries
            keys_to_remove = list(self._bar_start_cache.keys())[:5000]
            for key in keys_to_remove:
                del self._bar_start_cache[key]

        return bar_start

    def is_bar_complete(
        self, bar_start: datetime, current_time: datetime, timeframe: str
    ) -> bool:
        """Check if bar is complete"""
        bar_duration = timedelta(
            minutes=TimeframeConfig.get_minutes(timeframe))
        bar_end = bar_start + bar_duration
        return current_time >= bar_end

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
            bar_start_time = self.get_bar_start_time(
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
            if self.is_bar_complete(bar_start_time, timestamp, timeframe):
                current_bar.is_complete = True

            updated_bars[timeframe] = current_bar
            closed_bars[timeframe] = bar_was_closed

        self._last_tick_time = timestamp
        return updated_bars, closed_bars

    def _archive_completed_bar(self, symbol: str, timeframe: str, bar: Bar):
        """
        Archive completed bar to history.

        PERFORMANCE: No manual limit check needed!
        deque(maxlen) automatically discards oldest when full (O(1)).
        """
        self.logger.verbose(
            f"🔍 [BAR ARCHIVED] {timeframe} bar closed: {bar.timestamp}")

        self.logger.verbose(
            f"📊 {bar.symbol} {bar.timeframe} archived | "
            f"{bar.timestamp[:16]} | Close: {bar.close:.5f} | Ticks: {bar.tick_count}"
        )

        history = self.completed_bars[timeframe][symbol]
        history.append(bar)  # ✅ Auto-trims if len > maxlen

        self.logger.verbose(f"   History size AFTER append: {len(history)}")

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
            self.completed_bars[timeframe] = defaultdict(
                lambda: deque(maxlen=self.max_history)
            )

        if symbol not in self.completed_bars[timeframe]:
            self.completed_bars[timeframe][symbol] = deque(
                maxlen=self.max_history)

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
