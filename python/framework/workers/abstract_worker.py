"""
FiniexTestingIDE - Abstract Blackbox Worker
Base class for all worker implementations
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List

from python.framework.workers.worker_performance_tracker import WorkerPerformanceTracker
from python.framework.types.market_data_types import Bar, TickData
from python.framework.types.worker_types import (
    WorkerResult, WorkerState, WorkerType)
from python.components.logger.scenario_logger import ScenarioLogger
from python.framework.utils.timeframe_config_utils import TimeframeConfig


class AbstactWorker(ABC):
    """
    Abstract base class for all blackbox workers

    Workers compute indicators/signals based on bar data
    """

    # Type-specific required config fields
    REQUIRED_CONFIG_FIELDS = {
        WorkerType.INDICATOR: ["periods"],
        WorkerType.API: ["endpoints"],      # Post-MVP
        WorkerType.EVENT: ["subscriptions"]  # Post-MVP
    }

    def __init__(
        self,
        name: str,
        logger: ScenarioLogger,
        parameters: Dict[str, Any] = None,
    ):
        """
        Initialize worker.

        Args:
            name: Worker name/identifier
            parameters: Worker-specific parameters
            logger: ScenarioLogger instance (REQUIRED)

        Raises:
            ValueError: If logger is None
        """
        if logger is None:
            raise ValueError(f"Worker '{name}' requires a logger instance")

        self.name = name
        self.parameters = parameters or {}
        self.state = WorkerState.IDLE
        self._last_result = None

        # Loggers
        self.logger = logger  # ScenarioLogger
        self.performance_logger: WorkerPerformanceTracker = None

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

    def set_performance_logger(self, logger: WorkerPerformanceTracker):
        """
        Set performance logger for this worker.

        Called by WorkerOrchestrator during initialization.

        Args:
            logger: PerformanceLogWorker instance
        """
        self.performance_logger = logger

    # ============================================
    # Factory Support Methods
    # ============================================

    @classmethod
    @abstractmethod
    def get_worker_type(cls) -> WorkerType:
        """
        Get worker type classification.

        MUST be overridden in every subclass.
        Forces explicit type declaration.
        """
        pass

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

    @classmethod
    def validate_config(cls, config: Dict[str, Any]) -> None:
        """
        Validate config has type-specific required fields.

        Raises ValueError if required fields missing.
        Called by WorkerFactory before instantiation.

        Args:
            config: Worker configuration dict

        Raises:
            ValueError: If required fields missing
        """
        worker_type = cls.get_worker_type()
        required_fields = cls.REQUIRED_CONFIG_FIELDS.get(worker_type, [])

        for field in required_fields:
            if field not in config:
                raise ValueError(
                    f"{worker_type.value} worker '{cls.__name__}' requires "
                    f"'{field}' in config"
                )

            # Validate 'periods' is not empty for INDICATOR
            if field == "periods" and not config[field]:
                raise ValueError(
                    f"INDICATOR worker '{cls.__name__}' requires non-empty "
                    f"'periods' dict (e.g. {{'M5': 20}})"
                )

            # Validate timeframe keys inside 'periods'
            if field == "periods":
                for tf in config[field].keys():
                    # uses our central registry
                    TimeframeConfig.normalize(tf)

    @classmethod
    def calculate_requirements(cls, config: Dict[str, Any]) -> Dict[str, int]:
        """
        Calculate warmup requirements from config WITHOUT creating instance.

        This is THE KEY METHOD that eliminates double worker creation:
        - Phase 0: Call this to get requirements (no instance needed)
        - Phase 6: Create actual worker instance for execution

        Override in subclass for custom logic (e.g., MACD max(fast, slow)).

        Args:
            config: Worker configuration dict

        Returns:
            Dict[timeframe, bars_needed] - e.g. {"M5": 20, "M30": 50}

        Example:
            >>> config = {"periods": {"M5": 20, "M30": 50}, "deviation": 0.02}
            >>> EnvelopeWorker.calculate_requirements(config)
            {"M5": 20, "M30": 50}
        """
        # Default implementation: Use 'periods' directly for INDICATOR
        if cls.get_worker_type() == WorkerType.INDICATOR:
            return config.get("periods", {})

        # Non-INDICATOR workers return empty (no warmup needed)
        return {}
