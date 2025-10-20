"""
FiniexTestingIDE - Performance Summary Log
Thread-safe container for performance statistics across scenarios

Key Features:
- Stores performance stats for multiple scenarios
- Thread-safe for parallel execution
- Maintains original scenario order (by array index)
- Provides aggregation methods
- Live stats access for progress display

Architecture:
- Created in strategy_runner.py
- Passed to BatchOrchestrator
- Filled by BatchOrchestrator._execute_single_scenario()
- Read by reporting classes
- Live access by LiveProgressDisplay

EXTENDED (Phase 1a):
- get_live_scenario_stats() for real-time progress display
- Thread-safe partial data access during execution
- Fully typed with LiveScenarioStats class (no more dicts!)
- ScenarioStatus enum for type-safe state management
"""

import threading
import time
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field

from python.framework.trading_env.portfolio_manager import AccountInfo
from python.framework.types.live_stats_types import LiveScenarioStats, ScenarioStatus
from python.framework.types.scenario_set_performance_types import ScenarioPerformanceStats


class ScenarioSetPerformanceManager:
    """
    Thread-safe container for performance statistics across all scenarios.

    Usage:
        # In strategy_runner.py
        perf_log = ScenarioSetPerformanceManager()

        # In BatchOrchestrator
        stats = ScenarioPerformanceStats(...)
        perf_log.add_scenario_stats(scenario_index=0, stats=stats)

        # After batch execution
        perf_log.set_metadata(execution_time=10.5, success=True)

        # In reporting
        all_stats = perf_log.get_all_scenarios()
        metadata = perf_log.get_metadata()

        # Live Progress Display
        live_stats = perf_log.get_live_scenario_stats(scenario_index=0)
    """

    def __init__(self):
        """Initialize empty performance log."""
        self._scenarios: Dict[int, ScenarioPerformanceStats] = {}
        # FIXED: Use RLock (Reentrant Lock) instead of Lock
        # get_all_live_stats() hält den Lock und ruft intern get_live_scenario_stats() auf,
        # die auch den Lock will → Deadlock.
        # RLock = Reentrant Lock erlaubt dem gleichen Thread den Lock mehrfach zu nehmen.
        self._lock = threading.RLock()  # <-- CHANGED from Lock to RLock

        # Metadata
        self._execution_time = 0.0
        self._success = True

        # Live tracking (for progress display) - NOW TYPED!
        self._live_stats: Dict[int, LiveScenarioStats] = {}
        self._scenario_start_times: Dict[int, float] = {}

    def set_metadata(self, execution_time: float, success: bool) -> None:
        """
        Set batch-level metadata.

        Args:
            execution_time: Total execution time in seconds
            success: Overall success status
        """
        with self._lock:
            self._execution_time = execution_time
            self._success = success

    def get_metadata(self) -> Dict[str, Any]:
        """
        Get batch-level metadata.

        Returns:
            Dict with execution_time, success, total_scenarios
        """
        with self._lock:
            return {
                'execution_time': self._execution_time,
                'success': self._success,
                'total_scenarios': len(self._scenarios)
            }

    def add_scenario_stats(self, scenario_index: int, stats: ScenarioPerformanceStats) -> None:
        """
        Add performance stats for a completed scenario.

        Args:
            scenario_index: Scenario array index
            stats: ScenarioPerformanceStats object
        """
        with self._lock:
            self._scenarios[scenario_index] = stats

            # Clear live tracking for this scenario
            if scenario_index in self._live_stats:
                del self._live_stats[scenario_index]
            if scenario_index in self._scenario_start_times:
                del self._scenario_start_times[scenario_index]

    def get_all_scenarios(self) -> List[ScenarioPerformanceStats]:
        """
        Get all scenario statistics in original order.

        Returns:
            List of ScenarioPerformanceStats, sorted by index
        """
        with self._lock:
            # Sort by index to maintain original order
            sorted_items = sorted(self._scenarios.items(), key=lambda x: x[0])
            return [stats for _, stats in sorted_items]

    # ============================================
    # NEW (Phase 1a): Live Progress Tracking
    # ============================================

    def start_scenario_tracking(self,
                                scenario_index: int,
                                scenario_name: str,
                                symbol: str) -> None:
        """
        Begin live tracking for a scenario.
        Call this when scenario execution starts.

        Args:
            scenario_index: Scenario array index
            scenario_name: Scenario name
            symbol: Trading symbol
        """
        with self._lock:
            self._scenario_start_times[scenario_index] = time.time()
            self._live_stats[scenario_index] = LiveScenarioStats(
                scenario_name=scenario_name,
                symbol=symbol,
                total_ticks=0,
                ticks_processed=0,
                progress_percent=0.0,
                total_trades=0,
                winning_trades=0,
                losing_trades=0,
                portfolio_value=0.0,
                initial_balance=0.0,
                status=ScenarioStatus.INITIALIZED
            )

    def set_live_status(self, scenario_index: int, status: ScenarioStatus) -> None:
        """
        Set the status of a running scenario.

        Args:
            scenario_index: Scenario array index
            status: New status (ScenarioStatus enum)
        """
        with self._lock:
            if scenario_index not in self._live_stats:
                return

            self._live_stats[scenario_index].status = status

    def set_total_ticks(self, scenario_index: int, total_ticks: int) -> None:
        """
        Set the total number of ticks for a scenario.

        Args:
            scenario_index: Scenario array index
            total_ticks: Total ticks to process
        """
        with self._lock:
            if scenario_index not in self._live_stats:
                return
            self._live_stats[scenario_index].total_ticks = total_ticks

    def set_portfolio_balance(self, scenario_index: int, initial_balance: float) -> None:
        """
        Set the initial portfolio balance for a scenario.

        Args:
            scenario_index: Scenario array index
            initial_balance: Starting balance
        """
        with self._lock:
            if scenario_index not in self._live_stats:
                return
            self._live_stats[scenario_index].initial_balance = initial_balance
            self._live_stats[scenario_index].portfolio_value = initial_balance

    def update_live_stats(self,
                          scenario_index: int,
                          ticks_processed: Optional[int] = None,
                          portfolio_stats: Optional[Dict[str, Any]] = None,
                          account_info: Optional[AccountInfo] = None) -> None:
        """
        Update live statistics during scenario execution.
        Thread-safe, can be called from worker threads.

        Args:
            scenario_index: Scenario array index
            ticks_processed: Current tick count (optional)
            portfolio_stats: Portfolio statistics dict (optional)
            account_info: Account information object (optional)
        """
        with self._lock:
            if scenario_index not in self._live_stats:
                return

            stats = self._live_stats[scenario_index]

            if ticks_processed is not None:
                stats.ticks_processed = ticks_processed

            if stats.total_ticks > 0:
                progress_percent = (stats.ticks_processed /
                                    stats.total_ticks) * 100.0
                stats.progress_percent = progress_percent
                if progress_percent >= 100:
                    stats.status = ScenarioStatus.COMPLETED

            if portfolio_stats is not None:
                stats.total_trades = portfolio_stats.get('total_trades', 0)
                stats.winning_trades = portfolio_stats.get('winning_trades', 0)
                stats.losing_trades = portfolio_stats.get('losing_trades', 0)

            if account_info is not None:
                stats.portfolio_value = account_info.equity

    def get_live_scenario_stats(self, scenario_index: int) -> Optional[LiveScenarioStats]:
        """
        Get live statistics for a running scenario.
        Used by LiveProgressDisplay for real-time updates.

        Args:
            scenario_index: Scenario array index

        Returns:
            LiveScenarioStats object or None if scenario not tracked
        """
        with self._lock:
            # Check if completed - convert to LiveScenarioStats for consistency
            if scenario_index in self._scenarios:
                scenario_status_object = self._scenarios[scenario_index]
                return LiveScenarioStats(
                    scenario_name=scenario_status_object.scenario_name,
                    symbol=scenario_status_object.symbol,
                    total_ticks=scenario_status_object.ticks_processed,
                    ticks_processed=scenario_status_object.ticks_processed,
                    progress_percent=100.0,
                    total_trades=scenario_status_object.portfolio_stats.get(
                        'total_trades', 0),
                    winning_trades=scenario_status_object.portfolio_stats.get(
                        'winning_trades', 0),
                    losing_trades=scenario_status_object.portfolio_stats.get(
                        'losing_trades', 0),
                    portfolio_value=scenario_status_object.portfolio_value,
                    initial_balance=scenario_status_object.initial_balance,
                    status=ScenarioStatus.COMPLETED
                )

            # Check if running
            if scenario_index not in self._live_stats:
                return None

            # Return the live stats object directly (it's already a LiveScenarioStats)
            return self._live_stats[scenario_index]

    def get_all_live_stats(self) -> List[LiveScenarioStats]:
        """
        Get live stats for all tracked scenarios.
        Used by LiveProgressDisplay to show all running scenarios.

        Returns:
            List of LiveScenarioStats objects
        """
        with self._lock:
            all_stats: List[LiveScenarioStats] = []

            # Get all scenario indices (both running and completed)
            all_indices = set(self._live_stats.keys()) | set(
                self._scenarios.keys())

            for idx in sorted(all_indices):
                stats = self.get_live_scenario_stats(idx)
                if stats:
                    all_stats.append(stats)

            return all_stats
