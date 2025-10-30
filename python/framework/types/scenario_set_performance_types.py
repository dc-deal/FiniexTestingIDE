"""
FiniexTestingIDE - Scenario Performance Types
Type definitions for scenario execution statistics

FULLY TYPED: Uses dataclasses from trading_env_types instead of generic dicts.
ProfilingData now fully typed instead of Dict[str, Any]
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from python.framework.types.trading_env_types import PortfolioStats, ExecutionStats, CostBreakdown


@dataclass
class OperationTiming:
    """
    Timing data for a single operation in tick loop.

    Represents profiling data for one operation (e.g., 'worker_decision',
    'bar_rendering') collected during scenario execution.

    Attributes:
        total_time_ms: Total time spent in this operation (milliseconds)
        call_count: Number of times this operation was called
    """
    total_time_ms: float
    call_count: int

    @property
    def avg_time_ms(self) -> float:
        """
        Average time per call.

        Returns:
            Average time in milliseconds, or 0.0 if no calls
        """
        return self.total_time_ms / self.call_count if self.call_count > 0 else 0.0


@dataclass
class ProfilingData:
    """
    Performance profiling data from scenario execution.

    Built AFTER tick loop completes (zero overhead during ticks).
    Contains timing for each operation in the tick loop.

    Operations tracked:
    - trade_simulator: Price updates
    - bar_rendering: OHLC bar construction
    - bar_history: Historical bar retrieval
    - worker_decision: Worker processing + decision logic
    - order_execution: Order placement and execution
    - stats_update: Statistics updates

    Attributes:
        operations: Map of operation_name -> OperationTiming
        total_per_tick_ms: Total time per tick across all operations
    """
    operations: Dict[str, OperationTiming]
    total_per_tick_ms: float

    def get_operation_time(self, operation_name: str) -> float:
        """
        Get total time for operation.

        Args:
            operation_name: Name of operation (e.g., 'worker_decision')

        Returns:
            Total time in milliseconds, or 0.0 if operation not found
        """
        timing = self.operations.get(operation_name)
        return timing.total_time_ms if timing else 0.0

    def get_operation_count(self, operation_name: str) -> int:
        """
        Get call count for operation.

        Args:
            operation_name: Name of operation (e.g., 'worker_decision')

        Returns:
            Call count, or 0 if operation not found
        """
        timing = self.operations.get(operation_name)
        return timing.call_count if timing else 0

    @classmethod
    def from_dicts(
        cls,
        profile_times: Dict[str, float],
        profile_counts: Dict[str, int]
    ) -> 'ProfilingData':
        """
        Build ProfilingData from raw dict data (AFTER loop).

        This is called ONCE after tick loop completes.
        Zero overhead during tick processing.

        Args:
            profile_times: Map of operation_name -> total_time_ms
            profile_counts: Map of operation_name -> call_count

        Returns:
            ProfilingData instance

        Example:
            >>> times = {'worker_decision': 123.45, 'bar_rendering': 67.89}
            >>> counts = {'worker_decision': 1000, 'bar_rendering': 1000}
            >>> profiling = ProfilingData.from_dicts(times, counts)
        """
        # Extract total_per_tick (special key)
        total_per_tick = profile_times.pop('total_per_tick', 0.0)

        # Build operations map
        operations = {
            name: OperationTiming(
                total_time_ms=time_ms,
                call_count=profile_counts.get(name, 0)
            )
            for name, time_ms in profile_times.items()
        }

        return cls(
            operations=operations,
            total_per_tick_ms=total_per_tick
        )
