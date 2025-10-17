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
"""

import threading
import time
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field

from python.framework.trading_env.portfolio_manager import AccountInfo


@dataclass
class ScenarioPerformanceStats:
    """
    Performance statistics for a single scenario.

    Contains all performance data that was previously in batch_results dict.
    """
    # Scenario metadata
    scenario_index: int  # Original position in scenario array
    scenario_name: str
    symbol: str

    # Execution stats
    ticks_processed: int
    signals_generated: int
    signals_gen_buy: int
    signals_gen_sell: int
    signal_rate: float
    success: bool
    portfolio_value: float
    initial_balance: float
    elapsed_time: float

    # Worker statistics
    worker_statistics: Dict[str, Any]

    # Decision logic
    decision_logic_name: str

    # Scenario requirement
    scenario_requirement: Dict[str, Any]

    # Optional: First 10 signals for inspection
    sample_signals: List[Dict] = field(default_factory=list)

    # Portfolio & Trading Stats (per scenario)
    # Each scenario gets its own TradeSimulator, stats stored here
    portfolio_stats: Dict[str, Any] = field(default_factory=dict)
    execution_stats: Dict[str, Any] = field(default_factory=dict)
    cost_breakdown: Dict[str, Any] = field(default_factory=dict)

    # NEW: Profiling data from tick loop
    # Structure:
    # {
    #     'profile_times': {'trade_simulator': 123.45, 'bar_rendering': 67.89, ...},
    #     'profile_counts': {'trade_simulator': 100, 'bar_rendering': 100, ...},
    #     'total_per_tick': 456.78
    # }
    profiling_data: Dict[str, Any] = field(default_factory=dict)


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

        # Live tracking (for progress display)
        self._live_stats: Dict[int, Dict[str, Any]] = {}
        self._scenario_start_times: Dict[int, float] = {}

    def set_metadata(self, execution_time: float, success: bool):
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

    def add_scenario_stats(self, scenario_index: int, stats: ScenarioPerformanceStats):
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

    def start_scenario_tracking(self, scenario_index: int, scenario_name: str,
                                initial_balance: float,
                                total_ticks: int, symbol: str):
        """
        Begin live tracking for a scenario.
        Call this when scenario execution starts.

        Args:
            scenario_index: Scenario array index
            scenario_name: Scenario name
            total_ticks: Expected total ticks to process
            symbol: Trading symbol
        """
        with self._lock:
            self._scenario_start_times[scenario_index] = time.time()
            self._live_stats[scenario_index] = {
                'scenario_name': scenario_name,
                'symbol': symbol,
                'total_ticks': total_ticks,
                'ticks_processed': 0,
                'progress_percent': 0,
                'total_trades': 0,
                'winning_trades': 0,
                'losing_trades': 0,
                'portfolio_value': initial_balance,  # Default starting capital
                'initial_balance': initial_balance,
                'status': 'warmup'
            }

    def set_live_status(self, scenario_index: int, status: str = "warmup"):
        with self._lock:
            if scenario_index not in self._live_stats:
                return

            stats = self._live_stats[scenario_index]
            stats['status'] = status

    def update_live_stats(self, scenario_index: int,
                          ticks_processed: Optional[int] = None,
                          portfolio_stats: Dict = None,
                          account_info: AccountInfo = None):
        """
        Update live statistics during scenario execution.
        Thread-safe, can be called from worker threads.

        Args:
            scenario_index: Scenario array index
            ticks_processed: Current tick count (optional)
            trades_count: Current trade count (optional)
            portfolio_value: Current portfolio value (optional)
        """
        with self._lock:
            if scenario_index not in self._live_stats:
                return

            stats = self._live_stats[scenario_index]
            total_ticks = stats['total_ticks']
            if ticks_processed is not None:
                stats['ticks_processed'] = ticks_processed
            if total_ticks is not None and total_ticks > 0:
                progress_percent = ticks_processed / total_ticks * 100
                stats['progress_percent'] = progress_percent
                if (progress_percent >= 100):
                    stats['status'] = 'completed'
            if portfolio_stats is not None:
                stats['total_trades'] = portfolio_stats['total_trades']
                stats['winning_trades'] = portfolio_stats['winning_trades']
                stats['losing_trades'] = portfolio_stats['losing_trades']
            if account_info is not None:
                stats['portfolio_value'] = account_info.equity
            if total_ticks is not None:
                stats['total_ticks'] = total_ticks

    def get_live_scenario_stats(self, scenario_index: int) -> Optional[Dict[str, Any]]:
        """
        Get live statistics for a running scenario.
        Used by LiveProgressDisplay for real-time updates.

        Args:
            scenario_index: Scenario array index

        Returns:
            Dict with live stats or None if scenario not tracked:
            {
                'scenario_name': str,
                'symbol': str,
                'total_ticks': int,
                'ticks_processed': int,
                'progress_percent': float,
                'elapsed_time': float,
                'total_trades': int,
                'portfolio_value': float,
                'status': str  # 'running' or 'completed' or 'warmup'
            }
        """
        with self._lock:
            # Check if completed
            if scenario_index in self._scenarios:
                scenario_status_object = self._scenarios[scenario_index]
                return {
                    'scenario_name': scenario_status_object.scenario_name,
                    'symbol': scenario_status_object.symbol,
                    'total_ticks': scenario_status_object.ticks_processed,
                    'ticks_processed': scenario_status_object.ticks_processed,
                    'progress_percent': 100.0,
                    'elapsed_time': scenario_status_object.elapsed_time,  # Not tracked for completed
                    'total_trades': scenario_status_object.portfolio_stats.get('total_trades', 0),
                    'winning_trades': scenario_status_object.portfolio_stats.get('winning_trades', 0),
                    'losing_trades': scenario_status_object.portfolio_stats.get('losing_trades', 0),
                    'portfolio_value': scenario_status_object.portfolio_value,
                    'initial_balance': scenario_status_object.initial_balance,
                    'status': 'completed'
                }

            # Check if running
            if scenario_index not in self._live_stats:
                return None

            stats = self._live_stats[scenario_index]
            start_time = self._scenario_start_times.get(
                scenario_index, time.time())
            elapsed = time.time() - start_time

            progress = 0.0
            if stats['total_ticks'] > 0:
                progress = (stats['ticks_processed'] /
                            stats['total_ticks']) * 100.0

            return {
                'scenario_name': stats['scenario_name'],
                'symbol': stats['symbol'],
                'total_ticks': stats['total_ticks'],
                'ticks_processed': stats['ticks_processed'],
                'progress_percent': progress,
                'elapsed_time': elapsed,
                'total_trades': stats['total_trades'],
                'winning_trades': stats['winning_trades'],
                'losing_trades':  stats['losing_trades'],
                'portfolio_value': stats['portfolio_value'],
                'initial_balance': stats['initial_balance'],
                'status': stats['status']
            }

    def get_all_live_stats(self) -> List[Dict[str, Any]]:
        """
        Get live stats for all tracked scenarios.
        Used by LiveProgressDisplay to show all running scenarios.

        Returns:
            List of scenario stats dicts (see get_live_scenario_stats)
        """
        with self._lock:
            all_stats = []

            # Get all scenario indices (both running and completed)
            all_indices = set(self._live_stats.keys()) | set(
                self._scenarios.keys())

            for idx in sorted(all_indices):
                stats = self.get_live_scenario_stats(idx)
                if stats:
                    stats['scenario_index'] = idx
                    all_stats.append(stats)

            return all_stats
