"""
FiniexTestingIDE - Bar Rendering Controller
Main orchestrator for bar rendering system: renders bars from ticks for the timeframes the
registered workers require, holds their history, and takes the warmup history at startup.
The symbol comes from the configuration, never from a bar dict.
"""
from datetime import datetime
from typing import Callable, Dict, FrozenSet, List, Optional, Sequence, Set

from python.framework.bars.bar_renderer import BarRenderer
from python.framework.exceptions.warmup_history_errors import WarmupHistoryDefectError
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.types.market_types.market_data_types import Bar, BarRenderState, TickData
from python.framework.utils.bar_period import is_complete_at, open_time_of


class BarRenderingController:
    """
    Main orchestrator for bar rendering system.

    Manages:
    - Bar rendering via BarRenderer
    - Warmup bar injection
    - Bar history caching for performance
    - Worker registration and requirements
    """

    def __init__(self, logger: ScenarioLogger, max_history: int = 1000):
        """
        Initialize bar rendering controller.

        Args:
            logger: ScenarioLogger for this scenario
            max_history: Max bars to retain per symbol/timeframe
        """
        self.bar_renderer = BarRenderer(logger, max_history=max_history)
        self._workers = []
        self._required_timeframes = set()
        self.logger = logger

        # PERFORMANCE OPTIMIZATION: Bar history caching
        # Cache is built on first get_all_bar_history() call
        # and invalidated whenever a bar closes
        self._cached_bar_history: Dict[str, List[Bar]] = {}
        # CASHE PER TIMEFRAME!
        self._cache_valid_per_timeframe: Dict[str, bool] = {}

        # Bar-close transitions accumulated since the last algo pass consumed them.
        # Accumulating (not per-tick) keeps a close from being lost when the tick
        # that closed the bar is clipped (skips the algo path) — the close survives
        # until the next non-clipped pass calls consume_bar_render_state().
        self._pending_closed_timeframes: Set[str] = set()

        # Closes the gap between the warmup history and the first tick, once, before that
        # tick is rendered (registered by WarmupHistory; None once it has run)
        self._warmup_seam: Optional[Callable[[datetime], None]] = None

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
            f'Registered {len(workers)} workers requiring timeframes: {self._required_timeframes}'
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
        if self._warmup_seam is not None:
            close_seam, self._warmup_seam = self._warmup_seam, None
            close_seam(tick_data.timestamp)

        current_bars, closed_bars = self.bar_renderer.update_current_bars(
            tick_data, self._required_timeframes
        )

        # Invalidate cache ONLY for timeframes that closed + record the close
        # transition for the next algo pass (ON_BAR_CLOSE worker recompute trigger)
        for timeframe, was_closed in closed_bars.items():
            if was_closed:
                self.logger.verbose(
                    f'🔍 [CACHE INVALIDATED] {timeframe} bar closed')
                self._cache_valid_per_timeframe[timeframe] = False
                self._pending_closed_timeframes.add(timeframe)

        return current_bars

    def get_pending_closed_timeframes(self) -> FrozenSet[str]:
        """
        The timeframes whose bar closed since the last consume — read without consuming them.

        The session's state frames (#579) ask it between rendering a tick's bars and the algo
        pass that consumes the set, so a frame can name the bar the decision just saw close.

        Returns:
            A frozen copy of the set
        """
        return frozenset(self._pending_closed_timeframes)

    def consume_bar_render_state(self) -> BarRenderState:
        """
        Return the bar-lifecycle transitions since the last consume, and clear them.

        Read once per algo pass (shared pipeline core). The accumulated close set
        carries over clipped ticks and idle heartbeats, so a bar close is delivered
        to the worker orchestrator on the next pass that actually runs.

        Returns:
            BarRenderState with the timeframes whose bar closed since the previous consume
        """
        state = BarRenderState(closed_timeframes=self._pending_closed_timeframes)
        self._pending_closed_timeframes = set()
        return state

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
        bars_by_timeframe: Dict[str, Sequence[Bar]],
        reference_time: datetime,
    ) -> None:
        """
        Set the warmup history — the ONE route into the renderer's history, in both pipelines.

        Checks every bar before it enters: complete at the reference instant (its period has
        ended by then), and opening instants strictly increasing. A bar failing either is a
        defect in the selection that produced it, so the history is refused whole rather than
        trimmed — a trimmed history would hide the defect and still differ from every other
        path. Each bar's is_complete is stamped here, from the rule, once it has passed.

        Args:
            symbol: Trading symbol from config.symbol (authoritative)
            bars_by_timeframe: {timeframe: bars, oldest first}
            reference_time: The instant the history was selected for

        Returns:
            None
        """
        for timeframe, bars in bars_by_timeframe.items():
            self._require_complete_history(symbol, timeframe, bars, reference_time)
            for bar in bars:
                bar.is_complete = True
            self.bar_renderer.initialize_historical_bars(
                symbol=symbol,
                timeframe=timeframe,
                bars=bars
            )
            # Invalidate cache for this timeframe after injection
            self._cache_valid_per_timeframe[timeframe] = False

        self.logger.debug(
            f"✅ Injected warmup bars: "
            f"{', '.join(f'{tf}:{len(bars)}' for tf, bars in bars_by_timeframe.items())}"
        )

    def register_warmup_seam(self, close_seam: Callable[[datetime], None]) -> None:
        """
        Register what closes the gap between the warmup history and the first tick.

        Called with the first tick's instant, once, before that tick is rendered — the moment
        the session's own bars begin. Lives here rather than in either tick loop because both
        loops render through this controller, so the seam closes at the same point in both.

        Args:
            close_seam: Called once with the first tick's timestamp

        Returns:
            None
        """
        self._warmup_seam = close_seam

    @staticmethod
    def _require_complete_history(
        symbol: str,
        timeframe: str,
        bars: Sequence[Bar],
        reference_time: datetime,
    ) -> None:
        """
        Refuse a warmup history that holds an unfinished, repeated or out-of-order bar.

        Args:
            symbol: Trading symbol
            timeframe: The bars' timeframe
            bars: The bars, oldest first
            reference_time: The instant they were selected for

        Returns:
            None
        """
        previous_open: Optional[datetime] = None
        for bar in bars:
            open_time = open_time_of(bar)
            if bar.timeframe != timeframe:
                raise WarmupHistoryDefectError(
                    f'{symbol} {timeframe}: a {bar.timeframe} bar ({bar.timestamp}) was handed '
                    f'in as part of the {timeframe} warmup history.')
            if not is_complete_at(open_time, timeframe, reference_time):
                raise WarmupHistoryDefectError(
                    f'{symbol} {timeframe}: warmup bar {bar.timestamp} is not complete at '
                    f'{reference_time.isoformat()}, the instant the history was selected for — '
                    f'its high, low and close come from after that instant.')
            if previous_open is not None and open_time <= previous_open:
                raise WarmupHistoryDefectError(
                    f'{symbol} {timeframe}: warmup bar {bar.timestamp} does not follow '
                    f'{previous_open.isoformat()} — the history repeats or reorders a period.')
            previous_open = open_time
