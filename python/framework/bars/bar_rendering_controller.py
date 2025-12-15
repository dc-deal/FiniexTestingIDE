"""
FiniexTestingIDE - Bar Rendering Controller (WARMUP INJECTION FIX)
Main orchestrator for bar rendering system

CORRECTIONS:
- inject_warmup_bars() now properly initializes bar history
- Uses symbol from config (not bar_dict) as single source of truth
- Correctly fills _warmup_data for get_all_bar_history()
- Explicitly invalidates cache after warmup injection
- Calls bar_renderer.initialize_historical_bars() for each timeframe
- deserialize_bars_batch() moved here to avoid circular import
"""
from typing import Any, Dict, List, Optional, Tuple

from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.bars.bar_renderer import BarRenderer
from python.framework.types.market_data_types import Bar, TickData
from python.framework.utils.process_serialization_utils import deserialize_bars_batch


class BarRenderingController:
    """
    Main orchestrator for bar rendering system.

    Manages:
    - Bar rendering via BarRenderer
    - Warmup bar injection
    - Bar history caching for performance
    - Worker registration and requirements
    """

    def __init__(self, logger: ScenarioLogger):
        """
        Initialize bar rendering controller.

        Args:
            logger: ScenarioLogger for this scenario
        """
        self.bar_renderer = BarRenderer(logger)
        self._workers = []
        self._required_timeframes = set()
        self._warmup_data = {}  # Stores warmup bars for get_all_bar_history()
        self._warmup_quality_metrics = {}
        self.logger = logger

        # PERFORMANCE OPTIMIZATION: Bar history caching
        # Cache is built on first get_all_bar_history() call
        # and invalidated whenever a bar closes
        self._cached_bar_history: Dict[str, List[Bar]] = {}
        # CASHE PER TIMEFRAME!
        self._cache_valid_per_timeframe: Dict[str, bool] = {}

    def register_workers(self, workers):
        """
        Register workers and analyze their bar requirements.

        Workers specify which timeframes they need (e.g., RSI needs M5).
        This determines which bars need to be rendered.

        Args:
            workers: List of worker instances
        """
        self._workers = workers
        self._required_timeframes = self.bar_renderer.get_required_timeframes(
            workers)

        self.logger.debug(
            f"Registered {len(workers)} workers requiring timeframes: {self._required_timeframes}"
        )

    def process_tick(self, tick_data: TickData) -> Dict[str, Bar]:
        """
        Process tick and update all current bars.

        OPTIMIZED: Returns info about which bars were closed to enable caching.

        Args:
            tick_data: Current tick to process

        Returns:
            Dict[timeframe, Bar] - Updated current bars
        """
        current_bars, closed_bars = self.bar_renderer.update_current_bars(
            tick_data, self._required_timeframes
        )

        # Invalidate cache ONLY for timeframes that closed
        for timeframe, was_closed in closed_bars.items():
            if was_closed:
                self.logger.verbose(
                    f"🔍 [CACHE INVALIDATED] {timeframe} bar closed")
                self._cache_valid_per_timeframe[timeframe] = False

        return current_bars

    def get_bar_history(
        self, symbol: str, timeframe: str
    ) -> List[Bar]:
        """
        Get bar history (completed bars) for specific timeframe.

        Args:
            symbol: Trading symbol
            timeframe: Timeframe (e.g., "M5")

        Returns:
            List of completed Bar objects
        """
        return self.bar_renderer.get_bar_history(symbol, timeframe)

    def get_current_bar(self, symbol: str, timeframe: str) -> Optional[Bar]:
        """
        Get current (incomplete) bar for specific timeframe.

        Args:
            symbol: Trading symbol
            timeframe: Timeframe (e.g., "M5")

        Returns:
            Current Bar object or None if no bar yet
        """
        return self.bar_renderer.get_current_bar(symbol, timeframe)

    def get_warmup_quality_metrics(self) -> Dict:
        """
        Get quality metrics for warmup bars.

        Quality metrics include counts of synthetic/real bars.
        Used for debugging and validation.

        Returns:
            Dict[timeframe, quality_stats] with bar type breakdowns
        """
        return self._warmup_quality_metrics

    def get_all_bar_history(self, symbol: str) -> Dict[str, List[Bar]]:
        """
        Get all loaded warmup bars per timeframe.

        PERFORMANCE OPTIMIZED:
        Only rebuilds cache for timeframes where bars were closed.
        M5 closes don't trigger M30 rebuilds and vice versa.
        """
        result = {}

        for timeframe in self._required_timeframes:
            # Check if this specific timeframe's cache is valid
            is_valid = self._cache_valid_per_timeframe.get(timeframe, False)
            has_cache = timeframe in self._cached_bar_history

            if is_valid and has_cache:
                # Cache hit for this timeframe
                result[timeframe] = self._cached_bar_history[timeframe]
            else:
                # Cache miss - rebuild only this timeframe
                history = self.bar_renderer.get_bar_history(symbol, timeframe)
                self._cached_bar_history[timeframe] = history
                self._cache_valid_per_timeframe[timeframe] = True
                result[timeframe] = history

        return result

    def inject_warmup_bars(
        self,
        symbol: str,
        warmup_bars: Dict[str, Tuple[Any, ...]]
    ) -> None:
        """
        Inject prepared warmup bars WITHOUT validation.

        REPLACES: prepare_warmup_from_parquet_bars() in ProcessPool mode.

        Three critical operations:
        1. Store warmup_bars in _warmup_data (for get_all_bar_history())
        2. Convert bar dicts to Bar objects (via deserialize_bars_batch)
        3. Initialize bar_renderer history (fills completed_bars deque)

        NO VALIDATION: Trusts SharedDataPreparator's pre-filtering.

        Args:
            symbol: Trading symbol from config.symbol (authoritative)
            warmup_bars: {timeframe: tuple_of_bar_dicts}

        Example:
            warmup_bars = {'M5': (...), 'M30': (...)}
            controller.inject_warmup_bars('EURUSD', warmup_bars)
        """
        # 1. Store metadata for get_all_bar_history()
        self._warmup_data = warmup_bars

        # 2. Convert bar dicts to Bar objects and initialize renderer
        for timeframe, bars_tuple in warmup_bars.items():
            # Deserialize using top-level function (no import needed)
            bars_list = deserialize_bars_batch(symbol, bars_tuple)

            # 3. Initialize bar history in BarRenderer
            self.bar_renderer.initialize_historical_bars(
                symbol=symbol,
                timeframe=timeframe,
                bars=bars_list
            )
            # Invalidate cache for this timeframe after injection
            self._cache_valid_per_timeframe[timeframe] = False

        self.logger.debug(
            f"✅ Injected warmup bars: "
            f"{', '.join(f'{tf}:{len(warmup_bars[tf])}' for tf in warmup_bars)}"
        )
