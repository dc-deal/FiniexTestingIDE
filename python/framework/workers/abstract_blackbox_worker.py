"""
FiniexTestingIDE - Abstract Blackbox Worker
Base class for all worker implementations
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from python.framework.types import (Bar, TickData,
                                    WorkerResult, WorkerState, WorkerType)


class AbstractBlackboxWorker(ABC):
    """
    Abstract base class for all blackbox workers

    Workers compute indicators/signals based on bar data
    """

    def __init__(self, name: str, parameters: Dict[str, Any] = None):
        """
        Initialize worker

        Args:
            name: Worker name/identifier
            parameters: Worker-specific parameters
        """
        self.name = name
        self.parameters = parameters or {}
        self.state = WorkerState.IDLE
        self._last_result = None

        # NEW: Performance logging (set by WorkerCoordinator)
        self.performance_logger: Optional['PerformanceLogWorker'] = None

    @abstractmethod
    def get_warmup_requirements(self) -> Dict[str, int]:
        """
        Get warmup requirements per timeframe.

        Calculated from instance parameters (e.g., self.period).

        Returns:
            Dict[timeframe, bars_needed]
            Example: {"M5": 20, "M15": 20}
        """
        pass

    @abstractmethod
    def get_required_timeframes(self) -> List[str]:
        """
        Get required timeframes for this worker instance.

        Calculated from instance parameters (e.g., self.timeframe).

        Returns:
            List of timeframe strings
            Example: ["M5"]
        """
        pass

    def get_max_computation_time_ms(self) -> float:
        """
        Get max computation time for this worker instance.

        Optional - für Monitoring/Timeouts.
        Default: 100ms

        Returns:
            Max computation time in milliseconds
        """
        return 100.0

    def get_required_timeframes(self) -> List[str]:
        """
        Get required timeframes for this worker

        Returns:
            List of timeframe strings (e.g., ['M1', 'M5'])
        """
        return ["M1"]  # Default

    def get_warmup_requirements(self) -> Dict[str, int]:
        """
        Get warmup requirements per timeframe

        Returns:
            Dict[timeframe, bars_needed]
        """

        return {}  # Default

    @abstractmethod
    def should_recompute(self, tick: TickData, bar_updated: bool) -> bool:
        """
        Determine if worker should recompute on this tick

        Args:
            tick: Current tick data
            bar_updated: Whether a bar was updated/completed

        Returns:
            True if recomputation needed
        """
        pass

    @abstractmethod
    def compute(
        self,
        tick: TickData,
        bar_history: Dict[str, List[Bar]],
        current_bars: Dict[str, Bar],
    ) -> WorkerResult:
        """
        Compute worker output based on bar data

        Args:
            tick: Current tick (for metadata/timestamp)
            bar_history: Historical bars per timeframe
            current_bars: Current bars per timeframe

        Returns:
            WorkerResult with computed value
        """
        pass

    def get_last_result(self) -> WorkerResult:
        """Get last computation result"""
        return self._last_result

    def set_state(self, state: WorkerState):
        """Update worker state"""
        self.state = state

    def set_performance_logger(self, logger: 'PerformanceLogWorker'):
        """
        Set performance logger for this worker.

        Called by WorkerCoordinator during initialization.

        Args:
            logger: PerformanceLogWorker instance
        """
        self.performance_logger = logger

    # ============================================
    # Factory Support Methods
    # ============================================

    def get_worker_type(self) -> WorkerType:
        """
        Get worker type classification for monitoring.

        Override in subclass if needed.

        Returns:
            WorkerType enum value
        """
        return WorkerType.COMPUTE

    @classmethod
    def get_default_parameters(cls) -> Dict[str, Any]:
        """
        Get default parameter values.

        Used by factory for validation and defaults.
        Override in subclass to provide defaults.

        Returns:
            Dict of default parameter values
        """
        return {}
