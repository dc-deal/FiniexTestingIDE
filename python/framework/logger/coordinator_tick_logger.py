"""
FiniexTestingIDE - Coordinator Tick Logger
Handles optimized logging for WorkerOrchestrator with intelligent bar history caching.
"""

import json
from dataclasses import asdict
from datetime import datetime
from typing import Dict, List, Any

from python.components.logger.scenario_logger import ScenarioLogger
from python.framework.types.decision_logic_types import Decision
from python.framework.types.log_level import LogLevel
from python.framework.types.market_data_types import Bar, TickData
from python.framework.types.worker_types import WorkerResult


class CoordinatorTickLogger:
    """
    Optimized logger for tick-by-tick data with intelligent caching.

    Features:
    - Always logs: tick, worker results, current bars, decision
    - Caches bar history: only logs when bars actually change
    - JSON format for easy parsing and verification
    """

    def __init__(self, logger: ScenarioLogger):
        """
        Initialize logger with caching capability.

        Args:
            logger: ScenarioLogger instance from DecisionLogic
        """
        self.logger = logger
        self.should_log = self.logger.should_logLevel(LogLevel.VERBOSE)

        # Cache for bar history (timeframe -> list of bars)
        # Used to detect changes and avoid redundant logging
        self._last_logged_bar_history: Dict[str, List[Bar]] = {}

    def log_tick_data(
        self,
        tick: TickData,
        worker_results: Dict[str, WorkerResult],
        current_bars: Dict[str, Bar],
        bar_history: Dict[str, List[Bar]],
        decision: Decision
    ) -> None:
        """
        Log complete tick data with intelligent bar history caching.

        Logging strategy:
        - Tick: Always logged (changes every tick)
        - Worker Results: Always logged (may change every tick)
        - Current Bars: Always logged (OHLC updates every tick)
        - Bar History: Only logged when changed (new bar completed)
        - Decision: Always logged

        Args:
            tick: Current tick data
            worker_results: Results from all workers
            current_bars: Current incomplete bars per timeframe
            bar_history: Historical complete bars per timeframe
            decision: Trading decision for this tick
        """
        # do not act if Log level too high
        if not self.should_log:
            return

        # Build base log structure (always included)
        log_data = self._build_log_structure(
            tick=tick,
            worker_results=worker_results,
            current_bars=current_bars,
            decision=decision
        )

        # Check if bar history changed (intelligent caching)
        bar_history_changed = self._has_bar_history_changed(bar_history)

        if bar_history_changed:
            # Bar history changed - include full data
            log_data["bar_history"] = {
                timeframe: [asdict(bar) for bar in bars]
                for timeframe, bars in bar_history.items()
            }
            log_data["bar_history_status"] = "UPDATED"

            # Update cache for next comparison
            self._update_cache(bar_history)
        else:
            # Bar history unchanged - skip logging (saves ~90% log size)
            log_data["bar_history_status"] = "UNCHANGED (cached)"

        # Log as structured JSON for easy parsing
        self.logger.verbose(f"TICK_DATA: {json.dumps(log_data, indent=2)}")

    def _build_log_structure(
        self,
        tick: TickData,
        worker_results: Dict[str, WorkerResult],
        current_bars: Dict[str, Bar],
        decision: Decision
    ) -> Dict[str, Any]:
        """
        Build the base log data structure.

        Args:
            tick: Current tick data
            worker_results: Worker computation results
            current_bars: Current bars per timeframe
            decision: Trading decision

        Returns:
            Dictionary with serialized data
        """
        return {
            "tick": tick.to_dict(),
            "worker_results": {
                name: asdict(result)
                for name, result in worker_results.items()
            },
            "current_bars": {
                timeframe: asdict(bar)
                for timeframe, bar in current_bars.items()
            },
            "decision": decision.to_dict()
        }

    def _has_bar_history_changed(
        self,
        bar_history: Dict[str, List[Bar]]
    ) -> bool:
        """
        Detect if bar history changed since last log.

        Change detection strategy:
        1. First time logging? → Changed
        2. Different timeframes? → Changed
        3. Different bar count? → Changed
        4. Last bar timestamp different? → Changed (most common)

        Args:
            bar_history: Current bar history per timeframe

        Returns:
            True if bar history changed, False if cached version is still valid
        """
        # First time logging - no cache exists
        if not self._last_logged_bar_history:
            return True

        # Check if timeframes changed
        current_timeframes = set(bar_history.keys())
        cached_timeframes = set(self._last_logged_bar_history.keys())

        if current_timeframes != cached_timeframes:
            return True

        # Check each timeframe for changes
        for timeframe, current_bars in bar_history.items():
            cached_bars = self._last_logged_bar_history.get(timeframe, [])

            # Different number of bars? (new bar completed)
            if len(current_bars) != len(cached_bars):
                return True

            # Check if last bar changed (most common case)
            # When a bar closes, its timestamp shifts to next period
            if current_bars and cached_bars:
                current_last_ts = current_bars[-1].timestamp
                cached_last_ts = cached_bars[-1].timestamp

                if current_last_ts != cached_last_ts:
                    return True

        # No changes detected - cache is still valid
        return False

    def _update_cache(self, bar_history: Dict[str, List[Bar]]) -> None:
        """
        Update the bar history cache after logging.

        Creates a deep copy to avoid reference issues.

        Args:
            bar_history: Bar history to cache
        """
        self._last_logged_bar_history = {
            timeframe: bars.copy()
            for timeframe, bars in bar_history.items()
        }
