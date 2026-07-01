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
from python.framework.types.worker_types import WorkerResult, WorkerState, WorkerType
from python.framework.logging.scenario_logger import ScenarioLogger


class AbstractWorker(ABC):
    """
    Abstract base class for all blackbox workers.

    The lean cross-type contract: identity, parameters, output schema, the
    consumed-output subscription (#425), metadata, and lifecycle. The compute
    surface itself is type-specific — bar-centric workers extend
    AbstractIndicatorWorker, external-data workers extend AbstractSignalWorker.
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

        # Consumed-output set, injected by the orchestrator from the decision
        # logic's declaration. None = no declaration = compute every output.
        self._requested_outputs: Optional[Set[str]] = None

    def get_last_result(self) -> WorkerResult:
        """Get last computation result"""
        return self._last_result

    def set_requested_outputs(self, keys: Set[str]) -> None:
        """
        Declare which output keys a consumer reads — gates optional-output work.

        Injected by the orchestrator from the decision logic's
        get_required_workers(). Once set, the worker may skip computing
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

        Covers the factory-injected worker_type and the type-specific structural
        fields (e.g. 'periods' for INDICATOR) — none of which appear in
        get_parameter_schema(). Type-specific bases extend this (the indicator
        base adds 'compute_basis').

        Returns:
            Set of reserved config keys that are not unknown parameters
        """
        reserved = {'worker_type'}
        reserved.update(cls.REQUIRED_CONFIG_FIELDS.get(cls.get_worker_type(), []))
        return reserved

    @classmethod
    def validate_config(cls, config: Dict[str, Any]) -> None:
        """
        Validate config has type-specific required fields.

        Raises ValueError if required fields missing. Type-specific bases extend
        this (the indicator base adds compute_basis + periods validation).
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

    @classmethod
    def calculate_requirements(cls, config: Dict[str, Any]) -> Dict[str, int]:
        """
        Calculate warmup requirements from config WITHOUT creating an instance.

        Base default: no warmup (empty). Bar-centric workers override —
        AbstractIndicatorWorker returns the 'periods' map.

        Args:
            config: Worker configuration dict

        Returns:
            Dict[timeframe, bars_needed] (empty for workers with no warmup)
        """
        return {}
