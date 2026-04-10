"""
FiniexTestingIDE - Abstract Decision Logic (Refactored)
Base class for all decision logic implementations

Decision Logic orchestrates worker results into trading decisions AND executes them.
This layer is separate from worker coordination - it focuses purely
on decision-making strategy AND trade execution, not on worker management.
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional

from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.decision_logic.decision_logic_performance_tracker import DecisionLogicPerformanceTracker
from python.framework.trading_env.decision_trading_api import DecisionTradingApi
from python.framework.types.decision_logic_types import AwarenessLevel, Decision, DecisionAwareness
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.market_types.market_types import TradingContext
from python.framework.types.trading_env_types.order_types import OrderResult, OrderType
from python.framework.types.parameter_types import InputParamDef, OutputParamDef, ValidatedParameters
from python.framework.types.performance_types.performance_stats_types import DecisionLogicStats
from python.framework.types.worker_types import WorkerResult
from python.framework.validators.parameter_validator import validate_parameters


class AbstractDecisionLogic(ABC):
    """
    Abstract base class for decision logic implementations.

    Decision Logic takes worker results, generates trading decisions,
    and executes them via DecisionTradingApi.

    Philosophy (from Worker Manifest):
    - Workers are atomic units (first level)
    - DecisionLogic orchestrates results (second level)
    - No sub-workers, no hidden dependencies

    Architecture:
    1. get_required_order_types() declares needed order types
    2. BatchOrchestrator validates against broker capabilities
    3. DecisionTradingApi is injected after validation
    4. compute() generates decisions
    5. execute_decision() executes trades (Template Method)
       - Calls _execute_decision_impl() (subclass implements this)
       - Automatically updates statistics
    """

    def __init__(
        self,
        name: str,
        logger: ScenarioLogger,
        config=None,
        trading_context: TradingContext = None
    ):
        """
        Initialize decision logic.

        Args:
            name: Decision logic name
            logger: ScenarioLogger instance (REQUIRED)
            config: ValidatedParameters or dict (auto-wrapped)
            trading_context: TradingContext (optional)

        Raises:
            ValueError: If logger is None
        """
        if logger is None:
            raise ValueError(
                f"DecisionLogic '{name}' requires a logger instance")

        self.name = name
        self._trading_context = trading_context

        # Set by DecisionLogicFactory when loaded from a file path
        # Used by WorkerOrchestrator to resolve relative worker references
        self._source_path: Optional[Path] = None

        # API and loggers
        self.trading_api: Optional[DecisionTradingApi] = None
        self.logger = logger
        self.performance_logger: DecisionLogicPerformanceTracker = None

        # --- Parameter access  ---
        if isinstance(config, ValidatedParameters):
            self.params = config
        else:
            self.params = ValidatedParameters(config or {})

        # Raw dict access preserved for WorkerOrchestrator._extract_decision_logic_type()
        # which reads: decision_logic.config['decision_logic_type']
        self.config = self.params.as_dict()

        # AwarenessChannel — ephemeral narration slot (single slot, last write wins)
        self._last_awareness: Optional[DecisionAwareness] = None

    # ============================================
    # abstractmethods
    # ============================================

    @classmethod
    @abstractmethod
    def get_required_order_types(cls, decision_logic_config: Dict[str, Any]) -> List[OrderType]:
        """
            Declare required order types WITHOUT creating instance.

            This allows validation BEFORE Trade Simulator creation.
            Mirrors Worker.calculate_requirements() pattern.

            Args:
                config: Decision logic configuration dict

            Returns:
                List of OrderType this logic will use

            Example:
                @classmethod
                def get_required_order_types(cls, decision_logic_config):
                    return [OrderType.MARKET]
            """
        pass

    # ============================================
    # Parameter Schema
    # ============================================

    @classmethod
    def get_parameter_schema(cls) -> Dict[str, InputParamDef]:
        """
        Declare parameter schema for validation and UX.

        Override in subclass to define decision logic parameters
        with types, ranges, and defaults.

        Returns:
            Dict[param_name, InputParamDef]
        """
        return {}

    @classmethod
    def get_output_schema(cls) -> Dict[str, OutputParamDef]:
        """
        Declare output schema for typed decision outputs.

        Override in subclass to define what compute() returns
        in Decision.outputs. Used by #228 Live Console UI
        to render decision state generically.

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

        Called in Phase 0 (static) and Phase 6 (factory).

        Args:
            config: Decision logic configuration dict
            strict: True = raise on boundary violations, False = warn only

        Returns:
            List of warning messages
        """
        schema = cls.get_parameter_schema()
        if not schema:
            return []
        return validate_parameters(
            config, schema, strict, context_name=cls.__name__
        )

    def execute_decision(
        self,
        decision: Decision,
        tick: TickData
    ) -> Optional[OrderResult]:
        """
        Execute trading decision via DecisionTradingApi (Template Method).

        This is a template method that:
        1. Calls _execute_decision_impl() (implemented by subclass)
        2. Automatically updates statistics
        3. Returns order result

        Subclasses should implement _execute_decision_impl() instead.

        Args:
            decision: Decision object from compute()
            tick: Current tick data

        Returns:
            OrderResult if order was sent, None if no trade
        """
        # Call implementation (subclass)
        order_result = self._execute_decision_impl(decision, tick)

        if order_result:
            # Track trade request in performance logger
            if self.performance_logger:
                self.performance_logger.record_trade_requested()

        return order_result

    @abstractmethod
    def _execute_decision_impl(
        self,
        decision: Decision,
        tick: TickData
    ) -> Optional[OrderResult]:
        """
        Implementation of decision execution (to be overridden by subclass).

        This is called by execute_decision() template method.
        Subclass implements the actual trading logic here.

        Args:
            decision: Decision object from compute()
            tick: Current tick data

        Returns:
            OrderResult if order was sent, None if no trade
        """
        pass

    # ============================================
    # Existing abstractmethods (unchanged)
    # ============================================

    @abstractmethod
    def get_required_worker_instances(self) -> Dict[str, str]:
        """
        Define required worker instances with exact names and types.

        This is the connection between DecisionLogic and configuration.
        The config MUST provide worker_instances with matching keys and types.

        Example:
            {
                "rsi_fast": "CORE/rsi",
                "envelope_main": "CORE/envelope"
            }

        Config must match exactly:
            "worker_instances": {
                "rsi_fast": "CORE/rsi",        # ✅ Same key, same type
                "envelope_main": "CORE/envelope"  # ✅ Same key, same type
            }

        Type override is NOT allowed - if DecisionLogic declares
        "rsi_fast": "CORE/rsi", config cannot use "CORE/macd" instead.

        Returns:
            Dict[instance_name, worker_type] - The exact worker instances
        """
        pass

    @abstractmethod
    def compute(
        self,
        tick: TickData,
        worker_results: Dict[str, WorkerResult],
    ) -> Decision:
        """
        Generate trading decision based on worker results.

        This is the core decision-making method. It receives all worker
        outputs and must return a structured Decision object.

        Args:
            tick: Current tick data
            worker_results: Dict[worker_name, WorkerResult] - All worker outputs
            current_bars: Current bars per timeframe
            bar_history: Historical bars per timeframe

        Returns:
            Decision object with action/confidence/reason
        """
        pass

    # ============================================
    # AwarenessChannel — ephemeral narration
    # ============================================

    def notify_awareness(
        self,
        message: str,
        level: AwarenessLevel = AwarenessLevel.INFO,
        reason_key: Optional[str] = None
    ) -> None:
        """
        Set the current awareness narration (single slot, last write wins).

        Called in compute() to tell the operator what the algo is "thinking".
        NOT for structural rejections (those go through OrderGuard).

        Args:
            message: Human-readable narration
            level: Visual severity (INFO/NOTICE/ALERT)
            reason_key: Optional machine-readable key for grouping
        """
        self._last_awareness = DecisionAwareness(
            message=message,
            level=level,
            reason_key=reason_key,
        )

    def get_last_awareness(self) -> Optional[DecisionAwareness]:
        """
        Read the last awareness narration (non-destructive).

        Returns:
            DecisionAwareness or None if never set
        """
        return self._last_awareness

    # ============================================
    # API Injection
    # ============================================

    def set_trading_api(self, trading_api: DecisionTradingApi) -> None:
        """
        Inject DecisionTradingApi after validation.

        This is called by BatchOrchestrator after verifying that
        the broker supports all required order types.

        Args:
            trading_api: Validated DecisionTradingApi instance
        """
        self.trading_api = trading_api

    def get_statistics(self) -> DecisionLogicStats:
        """
        Get decision logic statistics.

        Delegates to performance tracker which returns complete
        DecisionLogicStats (signals + performance).

        Returns:
            DecisionLogicStats with all metrics
        """
        if not self.performance_logger:
            # Fallback for tests without performance logger
            return DecisionLogicStats()

        return self.performance_logger.get_stats()

    def set_performance_logger(self, performance_logger: DecisionLogicPerformanceTracker) -> None:
        """
        Set performance logger for this decision logic.

        Called by WorkerOrchestrator to enable performance tracking.

        Args:
            logger: PerformanceLogDecisionLogic instance
        """
        self.performance_logger = performance_logger
