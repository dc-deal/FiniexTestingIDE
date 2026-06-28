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
    ComputeBasis, WorkerResult, WorkerState, WorkerType)
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

        # Compute basis (#420) — resolved lazily on first use and cached
        # (config 'compute_basis' override → the worker's declaration).
        self._compute_basis: Optional[ComputeBasis] = None

        # Consumed-output set, injected by the orchestrator from the decision
        # logic's declaration. None = no declaration = compute every output.
        self._requested_outputs: Optional[Set[str]] = None

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

    def get_compute_basis(self) -> ComputeBasis:
        """
        Effective compute basis for this worker instance (#420), cached.

        The per-instance config key 'compute_basis' overrides the worker's
        declaration (get_default_compute_basis): the same CORE worker class can be
        LIVE for a tick-reactive strategy and BAR_CLOSE for a bar-grid strategy,
        decided in the run config. Resolved once and cached — no per-tick lookup.

        Returns:
            ComputeBasis governing when/what the orchestrator recomputes this worker
        """
        if self._compute_basis is None:
            configured = self.parameters.get('compute_basis')
            self._compute_basis = (
                ComputeBasis(configured) if configured
                else self.get_default_compute_basis())
        return self._compute_basis

    def set_requested_outputs(self, keys: Set[str]) -> None:
        """
        Declare which output keys a consumer reads — gates optional-output work.

        Injected by the orchestrator from the decision logic's
        get_required_worker_signals(). Once set, the worker may skip computing
        outputs not in this set (its always-on core stays unconditional).

        Args:
            keys: Output keys the consumer reads from this worker instance
        """
        self._requested_outputs = set(keys)

    def wants_output(self, key: str) -> bool:
        """
        Whether an optional output should be computed for this instance.

        True when no consumer declaration was injected (compute all — the
        default that keeps existing strategies bit-identical) or the key is in
        the declared set.

        Args:
            key: Output key to test

        Returns:
            True if the output should be computed
        """
        return self._requested_outputs is None or key in self._requested_outputs

    def effective_bars(
        self,
        timeframe: str,
        bar_history: Dict[str, List[Bar]],
        current_bars: Dict[str, Bar],
        count: Optional[int] = None,
    ) -> List[Bar]:
        """
        Bars this worker computes on for a timeframe (#420).

        Completed history, plus the current (forming) bar when the basis is LIVE
        (intra-bar view); completed-bars-only under BAR_CLOSE (the value changes
        only on a bar close). Centralizes the append that was previously duplicated
        inline in every worker's compute().

        A window-bounded worker passes 'count' — the number of completed bars it
        actually reads — so only that tail is materialized. For a worker that uses
        just its last 'count' bars the result is identical to the full-history path,
        but the per-compute cost drops from O(bar_max_history) to O(count) instead
        of copying / scanning the whole history on every tick.

        Args:
            timeframe: Timeframe key
            bar_history: Completed bars per timeframe
            current_bars: Current (forming) bar per timeframe
            count: Completed-bar window to keep (None = full history)

        Returns:
            List of bars to compute on (history tail, plus the current bar when LIVE)
        """
        bars = bar_history.get(timeframe, [])
        if count is not None:
            bars = bars[-count:]
        if self.get_compute_basis() == ComputeBasis.LIVE:
            current_bar = current_bars.get(timeframe)
            if current_bar:
                bars = (bars if count is not None else list(bars)) + [current_bar]
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

    def get_default_compute_basis(self) -> ComputeBasis:
        """
        Declare the worker's compute basis (#420) — MUST be overridden by every worker.

        Instance method, so the declaration may depend on the worker's own config
        (self.params). Return ComputeBasis.LIVE (per-tick, intra-bar — the tick-native
        default that preserves existing-set determinism) or ComputeBasis.BAR_CLOSE
        (completed bars only, recompute on close — stable + cheap, only for consumers
        that read on the bar-close grid). The per-instance config key 'compute_basis'
        overrides this per run.

        Returns:
            The worker's declared ComputeBasis

        Raises:
            NotImplementedError: If a subclass does not override this method.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} must declare get_default_compute_basis(). "
            f"Return ComputeBasis.LIVE for a tick-reactive worker (intra-bar value, "
            f"e.g. band position from tick.mid) or ComputeBasis.BAR_CLOSE for a "
            f"completed-bar indicator read on the bar-close grid. "
            f"See docs/user_guides/worker_naming_doc.md."
        )

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
        reserved/structural key (compute_basis, periods, …) is a typo
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

        Covers the per-instance framework opt-in (compute_basis), the factory-injected
        worker_type, and the type-specific structural fields (e.g. 'periods' for
        INDICATOR) — none of which appear in get_parameter_schema().

        Returns:
            Set of reserved config keys that are not unknown parameters
        """
        reserved = {'compute_basis', 'worker_type'}
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
        # Reserved framework key: compute basis (per-instance opt-in, #420)
        compute_basis = config.get('compute_basis')
        if compute_basis is not None:
            valid = [c.value for c in ComputeBasis]
            if compute_basis not in valid:
                raise ValueError(
                    f"Worker '{cls.__name__}': invalid 'compute_basis' "
                    f"'{compute_basis}'. Allowed: {valid}"
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
