"""
FiniexTestingIDE - Abstract Decision Logic (REFACTORED)
Base class for all decision logic implementations

REFACTORED:
- Added get_required_order_types() as abstractmethod
- Added execute_decision() as abstractmethod
- Added set_trading_api() for API injection after validation
- Removed trading_env parameter (replaced by DecisionTradingAPI)

Decision Logic orchestrates worker results into trading decisions AND executes them.
This layer is separate from worker coordination - it focuses purely
on decision-making strategy AND trade execution, not on worker management.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from python.framework.performance.performance_log_decision_logic import PerformanceLogDecisionLogic
from python.framework.trading_env.decision_trading_api import DecisionTradingAPI
from python.framework.types.global_types import Bar, Decision, TickData, WorkerResult
from python.framework.trading_env.order_types import OrderType, OrderResult


class AbstractDecisionLogic(ABC):
    """
    Abstract base class for decision logic implementations.

    Decision Logic takes worker results, generates trading decisions,
    and executes them via DecisionTradingAPI.

    Philosophy (from Worker Manifest):
    - Workers are atomic units (first level)
    - DecisionLogic orchestrates results (second level)
    - No sub-workers, no hidden dependencies

    REFACTORED Architecture:
    1. get_required_order_types() declares needed order types
    2. BatchOrchestrator validates against broker capabilities
    3. DecisionTradingAPI is injected after validation
    4. compute() generates decisions
    5. execute_decision() executes trades (Template Method)
       - Calls _execute_decision_impl() (subclass implements this)
       - Automatically updates statistics

    Example:
        class SimpleConsensus(AbstractDecisionLogic):
            def get_required_worker_instances(self):
                return  {
                    "rsi_fast": "CORE/rsi",
                    "envelope_main": "CORE/envelope"
                }

            def get_required_order_types(self):
                return [OrderType.MARKET]

            def compute(self, tick, worker_results, bars, history):
                rsi = worker_results["RSI"].value
                if rsi < 30:
                    return Decision(action="BUY", confidence=0.8)
                return Decision(action="FLAT", confidence=0.5)

            def _execute_decision_impl(self, decision, tick):
                if decision.action == "BUY":
                    account = self.trading_api.get_account_info()
                    if account.free_margin < 1000:
                        return None
                    return self.trading_api.send_order(...)
                return None
    """

    def __init__(
        self,
        name: str,
        config: Dict[str, Any] = None
    ):
        """
        Initialize decision logic.

        REFACTORED: No longer accepts trading_env parameter.
        DecisionTradingAPI is injected later via set_trading_api().

        Args:
            name: Logic identifier (e.g., "simple_consensus")
            config: Logic-specific configuration
        """
        self.name = name
        self.config = config or {}
        self.trading_api = None  # Injected after validation
        self._statistics = {
            "decisions_made": 0,
            "buy_signals": 0,
            "sell_signals": 0,
            "flat_signals": 0,
            "orders_executed": 0,
            "orders_rejected": 0,
        }

        # Performance logging (set by WorkerCoordinator)
        self.performance_logger: PerformanceLogDecisionLogic = None

    # ============================================
    # REFACTORED: New abstractmethods
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

        # Automatically update statistics
        self._update_statistics(decision, order_result)

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

        Example:
            def _execute_decision_impl(self, decision, tick):
                if decision.action == "BUY":
                    account = self.trading_api.get_account_info()
                    if account.free_margin < 1000:
                        return None  # Not enough margin

                    return self.trading_api.send_order(
                        symbol=tick.symbol,
                        order_type=OrderType.MARKET,
                        direction=OrderDirection.BUY,
                        lots=0.1
                    )
                return None
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
                "rsi_fast": "CORE/rsi",        # ✓ Same key, same type
                "envelope_main": "CORE/envelope"  # ✓ Same key, same type
            }

        Type override is NOT allowed - if DecisionLogic declares
        "rsi_fast": "CORE/rsi", config cannot use "CORE/macd" instead.

        Returns:
            Dict[instance_name, worker_type] - The exact worker
        """
        pass

    @abstractmethod
    def compute(
        self,
        tick: TickData,
        worker_results: Dict[str, WorkerResult],
        current_bars: Dict[str, Bar],
        bar_history: Dict[str, List[Bar]],
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
    # REFACTORED: API Injection
    # ============================================

    def set_trading_api(self, trading_api: DecisionTradingAPI):
        """
        Inject DecisionTradingAPI after validation.

        This is called by BatchOrchestrator after verifying that
        the broker supports all required order types.

        Args:
            trading_api: Validated DecisionTradingAPI instance
        """
        self.trading_api = trading_api

    # ============================================
    # Statistics & Helpers (unchanged)
    # ============================================

    def get_statistics(self) -> Dict[str, Any]:
        """
        Get decision logic statistics.

        Returns:
            Dict with decision counts and performance metrics
        """
        return self._statistics.copy()

    def _update_statistics(self, decision: Decision, order_result: Optional[OrderResult] = None):
        """
        Update internal statistics after decision.

        Called automatically by execute_decision() template method.

        Args:
            decision: Decision that was made
            order_result: OrderResult if trade was executed (can be None)
        """
        self._statistics["decisions_made"] += 1

        if decision.action == "BUY":
            self._statistics["buy_signals"] += 1
        elif decision.action == "SELL":
            self._statistics["sell_signals"] += 1
        elif decision.action == "FLAT":
            self._statistics["flat_signals"] += 1

        # Track order execution
        if order_result:
            if order_result.is_success:
                self._statistics["orders_executed"] += 1
            elif order_result.is_rejected:
                self._statistics["orders_rejected"] += 1

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

    def set_performance_logger(self, logger: 'PerformanceLogDecisionLogic'):
        """
        Set performance logger for this decision logic.

        Called by WorkerCoordinator to enable performance tracking.

        Args:
            logger: PerformanceLogDecisionLogic instance
        """
        self.performance_logger = logger
