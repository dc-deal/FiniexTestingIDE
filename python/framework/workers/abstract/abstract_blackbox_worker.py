"""
FiniexTestingIDE - Abstract Blackbox Worker
Base class for all worker implementations
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List

from python.framework.types import (Bar, TickData, WorkerContract,
                                    WorkerResult, WorkerState)


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

    @abstractmethod
    def get_contract(self) -> WorkerContract:
        """
        Define worker contract (requirements and capabilities)

        Returns:
            WorkerContract with warmup needs, timeframes, etc.
        """
        pass

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
