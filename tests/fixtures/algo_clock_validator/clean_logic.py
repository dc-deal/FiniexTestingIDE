"""
Test fixture (#359): decision logic with no wall-clock reads — must pass clean.
"""

from typing import Any, Dict, List

from python.framework.decision_logic.abstract_decision_logic import AbstractDecisionLogic
from python.framework.types.trading_env_types.order_types import OrderType


class CleanLogic(AbstractDecisionLogic):
    """No wall-clock reads — §9 compliant."""

    @classmethod
    def get_required_order_types(cls, decision_logic_config: Dict[str, Any]) -> List[OrderType]:
        return [OrderType.MARKET]

    def get_required_worker_instances(self) -> Dict[str, str]:
        return {}

    def compute_tick(self, tick, worker_results):
        return None

    def _execute_decision_impl(self, decision, tick):
        return None
