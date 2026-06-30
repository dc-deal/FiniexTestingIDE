"""
Test fixture (#359): decision logic that reads wall-clock — must be flagged.
"""

from datetime import datetime
from typing import Any, Dict, List

from python.framework.decision_logic.abstract_decision_logic import AbstractDecisionLogic
from python.framework.types.trading_env_types.order_types import OrderType
from python.framework.types.worker_types import WorkerRequirement


class WallClockLogic(AbstractDecisionLogic):
    """Reads datetime.now() in compute() — §9 violation."""

    @classmethod
    def get_required_order_types(cls, decision_logic_config: Dict[str, Any]) -> List[OrderType]:
        return [OrderType.MARKET]

    def get_required_workers(self) -> Dict[str, WorkerRequirement]:
        return {}

    def compute_tick(self, tick, worker_results):
        self._last_seen = datetime.now()
        return None

    def _execute_decision_impl(self, decision, tick):
        return None
