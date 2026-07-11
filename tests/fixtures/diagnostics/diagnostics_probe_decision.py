"""
Test-only decision logic for the diagnostics CSV sink integration (#376).

Declares a diagnostics sink and appends exactly one row on the first tick, then
stays FLAT (no trades). Used to prove that both pipelines flush algo-declared
diagnostics CSVs to the run directory at run end.
"""

from typing import Any, Dict, List, Optional

from python.framework.decision_logic.abstract_decision_logic import AbstractDecisionLogic
from python.framework.types.decision_logic_types import Decision, DecisionLogicAction
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.trading_env_types.market_data_status_types import MarketDataStatus
from python.framework.types.trading_env_types.order_types import OrderResult, OrderType
from python.framework.types.worker_types import WorkerRequirement, WorkerResult


class DiagnosticsProbeDecision(AbstractDecisionLogic):
    """Writes one diagnostics row, never trades. Test fixture for #376."""

    def __init__(self, name, logger, config=None, trading_context=None):
        super().__init__(name, logger, config, trading_context=trading_context)
        self._wrote = False

    @classmethod
    def get_required_order_types(cls, decision_logic_config: Dict[str, Any]) -> List[OrderType]:
        return [OrderType.MARKET]

    def get_required_workers(self) -> Dict[str, WorkerRequirement]:
        return {'rsi_fast': WorkerRequirement.all('CORE/rsi')}

    def on_market_data_stale(self, status: MarketDataStatus) -> None:
        """Deliberate pass (#436): probe never trades — nothing to protect."""
        pass

    def compute_tick(
        self,
        tick: TickData,
        worker_results: Dict[str, WorkerResult],
    ) -> Decision:
        if not self._wrote:
            self.diagnostics_csv('probe_funnel', ['tick_time', 'note']).append_row({
                'tick_time': tick.timestamp.isoformat(),
                'note': 'probe',
            })
            self._wrote = True
        return Decision(action=DecisionLogicAction.FLAT, outputs={})

    def _execute_decision_impl(
        self,
        decision: Decision,
        tick: Optional[TickData],
    ) -> Optional[OrderResult]:
        return None
