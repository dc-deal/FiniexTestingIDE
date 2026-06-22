"""
FiniexTestingIDE - Abstract Blackbox Worker
Base class for all worker implementations
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Set

from python.framework.types.component_metadata_types import ComponentMetadata
from python.framework.types.market_types.market_types import TradingContext
from python.framework.types.parameter_types import InputParamDef, OutputParamDef, ValidatedParameters
from python.framework.validators.parameter_validator import validate_parameters
from python.framework.workers.worker_performance_tracker import WorkerPerformanceTracker
from python.framework.types.market_types.market_data_types import Bar, TickData
from python.framework.types.worker_types import (
    RecomputeCadence, WorkerResult, WorkerState, WorkerType)
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.utils.timeframe_config_utils import TimeframeConfig


class AbstractWorker(ABC):
    """
    Abstract base class for all blackbox workers

    Workers compute indicators/signals based on bar data
    """

    # Type-specific required config fields
    REQUIRED_CONFIG_FIELDS = {
        WorkerType.INDICATOR: ["periods"],
        WorkerType.API: ["endpoints"],      # Post-V1
        WorkerType.EVENT: ["subscriptions"]  # Post-V1
    }

    def __init__(
        self,
        name: str,
        logger: ScenarioLogger,
        parameters=None,
        trading_context: TradingContext = None
    ):
        """
        Initialize worker.

        Args:
            name: Worker name/identifier
            parameters: ValidatedParameters or dict (auto-wrapped)
            logger: ScenarioLogger instance (REQUIRED)
            trading_context: TradingContext (optional)

        Raises:
            ValueError: If logger is None
        """
        if logger is None:
            raise ValueError(f"Worker '{name}' requires a logger instance")

        self.name = name
        self.state = WorkerState.IDLE
        self._last_result = None
        self._trading_context = trading_context

        # Loggers
        self.logger = logger
        self.performance_logger: WorkerPerformanceTracker = None

        # --- Parameter access ---
        # Auto-wrap dict → ValidatedParameters for test convenience
        # Factory already provides ValidatedParameters; direct dict
        # construction (tests, legacy) gets wrapped transparently.
        if isinstance(parameters, ValidatedParameters):
            self.params = parameters
        else:
            self.params = ValidatedParameters(parameters or {})

        # Raw dict access preserved for WorkerOrchestrator._extract_worker_type()
        # which reads: worker.parameters['worker_type']
        self.parameters = self.params.as_dict()

        # --- Infrastructure: auto-extract 'periods' for INDICATOR workers ---
        # This eliminates the #1 boilerplate pattern across all INDICATOR workers
        # and prevents the OBV bug (missing self.periods) from ever recurring.
        if self.__class__.get_worker_type() == WorkerType.INDICATOR:
            self.periods = self.params.get('periods')

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
            WorkerResult with outputs dict matching get_output_schema() keys
        """
        pass

    def get_last_result(self) -> WorkerResult:
        """Get last computation result"""
        return self._last_result

    def get_recompute_cadence(self) -> RecomputeCadence:
        """
        Effective recompute cadence for this worker instance.

        The per-instance config key 'recompute' overrides the class default
        (get_default_recompute_cadence). This is the single per-instance switch:
        the same CORE worker class can be PER_TICK for a tick-reactive strategy
        and ON_BAR_CLOSE for a bar-close strategy, decided in the run config.

        Returns:
            RecomputeCadence governing when the orchestrator recomputes this worker
        """
        configured = self.parameters.get('recompute')
        if configured is None:
            return self.__class__.get_default_recompute_cadence()
        return RecomputeCadence(configured)

    def includes_current_bar(self) -> bool:
        """
        Whether this worker instance computes on the current (still-forming, incomplete)
        bar in addition to completed history (#387).

        The per-instance config key 'include_current_bar' overrides the class default
        (get_default_includes_current_bar). False = completed-bar-only — the worker's
        value changes only when a bar closes (the institutional bar-indicator model),
        which a bar-close consumer reads identically on any finer grid.

        Returns:
            True to append the current bar (default, live intra-bar view)
        """
        configured = self.parameters.get('include_current_bar')
        if configured is None:
            return self.__class__.get_default_includes_current_bar()
        return bool(configured)

    def effective_bars(
        self,
        timeframe: str,
        bar_history: Dict[str, List[Bar]],
        current_bars: Dict[str, Bar],
    ) -> List[Bar]:
        """
        Bars this worker computes on for a timeframe (#387).

        Completed history, plus the current (forming) bar unless the worker is
        configured completed-bar-only (include_current_bar=False). Centralizes the
        append that was previously duplicated inline in every worker's compute().

        Args:
            timeframe: Timeframe key
            bar_history: Completed bars per timeframe
            current_bars: Current (forming) bar per timeframe

        Returns:
            List of bars to compute on (history, optionally with the current bar)
        """
        bars = bar_history.get(timeframe, [])
        if self.includes_current_bar():
            current_bar = current_bars.get(timeframe)
            if current_bar:
                bars = list(bars) + [current_bar]
        return bars

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
    def get_required_activity_metric(cls) -> Optional[str]:
        """
        Declare the market activity metric this worker requires.

        MUST be overridden by every worker — no default, no silent fallback.
        The framework validates at pre-flight time that the scenario's broker
        provides this metric, by cross-referencing
        market_config.json → primary_activity_metric.

        Returns:
            Metric string ('volume', 'tick_count', ...) or None if the
            worker has no activity-data dependency (pure price-based
            workers like RSI, Bollinger, MACD).

        Raises:
            NotImplementedError: If subclass does not override this method.
        """
        raise NotImplementedError(
            f"{cls.__name__} must declare get_required_activity_metric(). "
            f"Return 'volume' if the worker consumes real trade volume, "
            f"'tick_count' if it depends on tick arrival density, or None "
            f"if it is purely price-based (RSI, Bollinger, MACD). "
            f"See docs/architecture/market_capabilities.md."
        )

    @classmethod
    def get_metadata(cls) -> ComponentMetadata:
        """
        Author-declared metadata (version, doc link, recommended market fit).

        Override to declare. Default is an empty ComponentMetadata (opt-in, no-op).
        Complements the automatic config_fingerprint with semantic intent.

        Returns:
            ComponentMetadata for this worker
        """
        return ComponentMetadata()

    @classmethod
    def get_default_recompute_cadence(cls) -> RecomputeCadence:
        """
        Natural recompute cadence for this worker class.

        Default PER_TICK — preserves the historical behavior and the determinism
        of existing scenario sets. A bar-derived worker MAY override this to
        ON_BAR_CLOSE, but CORE workers stay PER_TICK until the event-driven loop
        (#375) makes a bar-close default safe across all consumers. The per-instance
        config key 'recompute' overrides this per run.

        Returns:
            Default RecomputeCadence for this worker class
        """
        return RecomputeCadence.PER_TICK

    @classmethod
    def get_default_includes_current_bar(cls) -> bool:
        """
        Whether this worker class computes on the current (forming) bar by default (#387).

        Default True — preserves the historical live intra-bar behavior and the
        determinism of existing scenario sets. The institutional bar-indicator model
        is completed-bar-only (False); a worker MAY default to that, but CORE workers
        stay True until consumers migrate (same migration discipline as the recompute
        cadence default). The per-instance config key 'include_current_bar' overrides.

        Returns:
            Default current-bar inclusion for this worker class
        """
        return True

    @classmethod
    def get_parameter_schema(cls) -> Dict[str, InputParamDef]:
        """
        Declare parameter schema for validation and UX.

        Override in subclass to define algorithm parameters
        with types, ranges, and defaults.
        Does NOT include 'periods' (handled by validate_config).

        Returns:
            Dict[param_name, InputParamDef]
        """
        return {}

    @classmethod
    def get_output_schema(cls) -> Dict[str, OutputParamDef]:
        """
        Declare output parameter schema for typed access and display.

        Override in subclass to define output parameters
        with type, range, category, and display hints.

        Returns:
            Dict[output_name, OutputParamDef]
        """
        return {}

    @classmethod
    def validate_parameter_schema(
        cls,
        config: Dict[str, Any],
        strict: bool = True
    ) -> List[str]:
        """
        Validate config against parameter schema (no instance needed).

        Rejects unknown config keys — a key that is neither a schema parameter nor a
        reserved/structural key (recompute, include_current_bar, periods, …) is a typo
        that would otherwise be silently ignored at runtime.

        Called in Phase 0 (static) and Phase 6 (factory).

        Args:
            config: Worker configuration dict
            strict: True = raise on boundary violations, False = warn only

        Returns:
            List of warning messages
        """
        return validate_parameters(
            config, cls.get_parameter_schema(), strict,
            context_name=cls.__name__, reserved_keys=cls._reserved_config_keys(),
        )

    @classmethod
    def _reserved_config_keys(cls) -> Set[str]:
        """
        Non-schema config keys the framework accepts on a worker.

        Covers the per-instance framework opt-ins (recompute, include_current_bar),
        the factory-injected worker_type, and the type-specific structural fields
        (e.g. 'periods' for INDICATOR) — none of which appear in get_parameter_schema().

        Returns:
            Set of reserved config keys that are not unknown parameters
        """
        reserved = {'recompute', 'include_current_bar', 'worker_type'}
        reserved.update(cls.REQUIRED_CONFIG_FIELDS.get(cls.get_worker_type(), []))
        return reserved

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
        # Reserved framework key: recompute cadence (per-instance opt-in)
        recompute = config.get('recompute')
        if recompute is not None:
            valid = [c.value for c in RecomputeCadence]
            if recompute not in valid:
                raise ValueError(
                    f"Worker '{cls.__name__}': invalid 'recompute' cadence "
                    f"'{recompute}'. Allowed: {valid}"
                )

        # Reserved framework key: current-bar inclusion (per-instance opt-in)
        include_current_bar = config.get('include_current_bar')
        if include_current_bar is not None and not isinstance(include_current_bar, bool):
            raise ValueError(
                f"Worker '{cls.__name__}': 'include_current_bar' must be a bool, "
                f"got {type(include_current_bar).__name__} ({include_current_bar})"
            )

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
            >>> config = {"periods": {"M5": 20, "M30": 50}, "deviation": 2.0}
            >>> BollingerWorker.calculate_requirements(config)
            {"M5": 20, "M30": 50}
        """
        # Default implementation: Use 'periods' directly for INDICATOR
        if cls.get_worker_type() == WorkerType.INDICATOR:
            return config.get("periods", {})

        # Non-INDICATOR workers return empty (no warmup needed)
        return {}
