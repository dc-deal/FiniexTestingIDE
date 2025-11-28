"""
FiniexTestingIDE - Abstract Decision Logic (Refactored)
Base class for all decision logic implementations

CHANGES:
- Added get_required_order_types() as abstractmethod
- Added execute_decision() as abstractmethod (Template Method pattern)
- Added set_trading_api() for API injection after validation
- Removed trading_env parameter (replaced by DecisionTradingAPI)
- REFACTORED: _statistics is now DecisionLogicStats dataclass (type-safe)
- REFACTORED: Uses DecisionLogicAction enum instead of string comparisons

Decision Logic orchestrates worker results into trading decisions AND executes them.
This layer is separate from worker coordination - it focuses purely
on decision-making strategy AND trade execution, not on worker management.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from python.components.logger.scenario_logger import ScenarioLogger
from python.framework.decision_logic.decision_logic_performance_tracker import DecisionLogicPerformanceTracker
from python.framework.trading_env.decision_trading_api import DecisionTradingAPI
from python.framework.types.decision_logic_types import Decision
from python.framework.types.market_data_types import TickData
from python.framework.types.order_types import OrderResult, OrderType
from python.framework.types.performance_stats_types import DecisionLogicStats
from python.framework.types.worker_types import WorkerResult


class AbstractDecisionLogic(ABC):
    """
    Abstract base class for decision logic implementations.

    Decision Logic takes worker results, generates trading decisions,
    and executes them via DecisionTradingAPI.

    Philosophy (from Worker Manifest):
    - Workers are atomic units (first level)
    - DecisionLogic orchestrates results (second level)
    - No sub-workers, no hidden dependencies

    Architecture:
    1. get_required_order_types() declares needed order types
    2. BatchOrchestrator validates against broker capabilities
    3. DecisionTradingAPI is injected after validation
    4. compute() generates decisions
    5. execute_decision() executes trades (Template Method)
       - Calls _execute_decision_impl() (subclass implements this)
       - Automatically updates statistics
    """

    def __init__(
        self,
        name: str,
        logger: ScenarioLogger,
        config: Dict[str, Any]
    ):
        """
        Initialize decision logic.

        Args:
            name: Decision logic name
            logger: ScenarioLogger instance (REQUIRED)
            config: Logic-specific configuration

        Raises:
            ValueError: If logger is None
        """
        if logger is None:
            raise ValueError(
                f"DecisionLogic '{name}' requires a logger instance")

        self.name = name
        self.config = config or {}

        # API and loggers
        self.trading_api: Optional[DecisionTradingAPI] = None
        self.logger = logger
        self.performance_logger: DecisionLogicPerformanceTracker = None

    # ============================================
    # New abstractmethods
    # ============================================

    @abstractmethod
    def get_required_order_types(self) -> List[OrderType]:
        """
        Declare which order types this logic will use.

        This is called BEFORE scenario starts to validate broker support.
        Prevents runtime failures from unsupported order types.

        Returns:
            List of OrderType that this logic needs

        Example:
            def get_required_order_types(self):
                return [OrderType.MARKET, OrderType.LIMIT]
        """
        pass

    def execute_decision(
        self,
        decision: Decision,
        tick: TickData
    ) -> Optional[OrderResult]:
        """
        Execute trading decision via DecisionTradingAPI (Template Method).

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
    # API Injection
    # ============================================

    def set_trading_api(self, trading_api: DecisionTradingAPI) -> None:
        """
        Inject DecisionTradingAPI after validation.

        This is called by BatchOrchestrator after verifying that
        the broker supports all required order types.

        Args:
            trading_api: Validated DecisionTradingAPI instance
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

    def get_config_value(self, key: str, default: Any = None) -> Any:
        """
        Get configuration value with default fallback.

        Helper method for accessing logic-specific config.

        Args:
            key: Config key
            default: Default value if key not found

        Returns:
            Config value or default
        """
        return self.config.get(key, default)

    def set_performance_logger(self, performance_logger: DecisionLogicPerformanceTracker) -> None:
        """
        Set performance logger for this decision logic.

        Called by WorkerOrchestrator to enable performance tracking.

        Args:
            logger: PerformanceLogDecisionLogic instance
        """
        self.performance_logger = performance_logger
