"""
USER Decision Logic Template — Copy, rename, and implement.

Steps:
1. Copy this file: cp TEMPLATE_logic.py my_strategy.py
2. Rename class: TEMPLATELogic → MyStrategy (no suffix needed)
3. Implement compute() and _execute_decision_impl() with your strategy
4. Reference in config: "USER/my_strategy"

See docs/user_guides/user_modules_and_hot_reload_mechanics.md for details.
See docs/user_guides/quickstart_guide.md for a full walkthrough.
"""

from typing import Any, Dict, List, Optional

from python.framework.decision_logic.abstract_decision_logic import AbstractDecisionLogic
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.types.decision_logic_types import Decision, DecisionLogicAction
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.market_types.market_types import TradingContext
from python.framework.types.parameter_types import InputParamDef, OutputParamDef
from python.framework.types.trading_env_types.order_types import (
    OrderDirection,
    OrderResult,
    OrderStatus,
    OrderType,
)
from python.framework.types.worker_types import WorkerResult


class TEMPLATELogic(AbstractDecisionLogic):
    """Replace with your strategy description."""

    def __init__(self, name, logger: ScenarioLogger, config,
                 trading_context: TradingContext = None):
        super().__init__(name, logger, config, trading_context=trading_context)
        # Access config values via self.params.get('my_param')

    # ============================================
    # Class methods for Factory
    # ============================================

    @classmethod
    def get_parameter_schema(cls) -> Dict[str, InputParamDef]:
        """Define your configurable parameters with validation ranges."""
        return {
            # 'lot_size': InputParamDef(
            #     param_type=float, default=0.1, min_val=0.01, max_val=100.0,
            #     description="Fixed lot size for orders"
            # ),
        }

    @classmethod
    def get_output_schema(cls) -> Dict[str, OutputParamDef]:
        """Define your decision output parameters."""
        return {
            # 'confidence': OutputParamDef(
            #     param_type=float, min_val=0.0, max_val=1.0,
            #     description='Signal confidence score',
            #     category='SIGNAL', display=True,
            # ),
            # 'reason': OutputParamDef(
            #     param_type=str,
            #     description='Human-readable decision explanation',
            #     category='INFO',
            # ),
        }

    @classmethod
    def get_required_order_types(cls, decision_logic_config: Dict[str, Any]) -> List[OrderType]:
        """Declare which order types your strategy needs."""
        return [OrderType.MARKET]

    def get_required_worker_instances(self) -> Dict[str, str]:
        """
        Declare which workers this strategy needs.

        Returns:
            Dict mapping instance_name → worker_type
        """
        return {
            # 'rsi_main': 'CORE/rsi',
            # 'my_custom': 'USER/my_worker',
        }

    # ============================================
    # Strategy logic
    # ============================================

    def compute(
        self,
        tick: TickData,
        worker_results: Dict[str, WorkerResult],
    ) -> Decision:
        """
        Generate a trading decision from worker results.

        Args:
            tick: Current tick data
            worker_results: Results from your declared workers

        Returns:
            Decision with action (BUY/SELL/FLAT) and typed outputs
        """
        return Decision(
            action=DecisionLogicAction.FLAT,
            outputs={
                'reason': 'Template not implemented',
                'price': tick.mid,
            },
        )

    def _execute_decision_impl(
        self,
        decision: Decision,
        tick: TickData,
    ) -> Optional[OrderResult]:
        """
        Execute the trading decision via self.trading_api.

        Args:
            decision: Decision from compute()
            tick: Current tick data

        Returns:
            OrderResult if order was sent, None if no trade
        """
        if decision.action == DecisionLogicAction.FLAT:
            return None

        # Example: send a market order
        # direction = (OrderDirection.LONG if decision.action == DecisionLogicAction.BUY
        #              else OrderDirection.SHORT)
        # return self.trading_api.send_order(
        #     symbol=tick.symbol,
        #     order_type=OrderType.MARKET,
        #     direction=direction,
        #     lots=self.params.get('lot_size'),
        #     comment=f"MyStrategy: {decision.get_signal('reason')[:50]}"
        # )
        return None
