"""
Test fixture: a strategy that names its worker relative to its own file and imports a sibling.

Both are what a deployed strategy does, and both resolve differently once the code tree and the
data home are apart. It never trades.
"""

from typing import Any, Dict, List, Optional

from python.framework.decision_logic.abstract_decision_logic import AbstractDecisionLogic
from python.framework.types.decision_logic_types import Decision, DecisionLogicAction
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.trading_env_types.market_data_status_types import MarketDataStatus
from python.framework.types.trading_env_types.order_types import OrderFeature, OrderResult, OrderType
from python.framework.types.worker_types import WorkerRequirement, WorkerResult
from user_algos.my_algo.my_math import ANSWER


class MyDecision(AbstractDecisionLogic):
    """Stays flat on every tick."""

    @classmethod
    def get_required_order_types(cls, decision_logic_config: Dict[str, Any]) -> List[OrderType]:
        return [OrderType.MARKET]

    @classmethod
    def get_required_order_features(cls, decision_logic_config: Dict[str, Any]) -> List[OrderFeature]:
        return []

    def get_required_workers(self) -> Dict[str, WorkerRequirement]:
        return {'my_worker': WorkerRequirement.all('my_worker.py')}

    def on_market_data_stale(self, status: MarketDataStatus) -> None:
        """Nothing is held, so nothing to protect."""
        pass

    def compute_tick(
        self,
        tick: TickData,
        worker_results: Dict[str, WorkerResult],
    ) -> Decision:
        return Decision(action=DecisionLogicAction.FLAT, outputs={'answer': ANSWER})

    def _execute_decision_impl(
        self,
        decision: Decision,
        tick: Optional[TickData],
    ) -> Optional[OrderResult]:
        return None
