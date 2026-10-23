"""
Test-only decision logic for the run entry census (#621).

Writes one diagnostics row on its first tick, opens one MARKET position at `open_tick` and closes
it at `close_tick` — enough to make a run write every member that hangs on an order or on a
declared sink, in both pipelines. It decides on nothing it reads.
"""

from typing import Any, Dict, List, Optional

from python.framework.decision_logic.abstract_decision_logic import AbstractDecisionLogic
from python.framework.types.decision_logic_types import Decision, DecisionLogicAction
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.parameter_types import InputParamDef
from python.framework.types.trading_env_types.market_data_status_types import MarketDataStatus
from python.framework.types.trading_env_types.order_types import OrderResult, OrderSide, OrderType
from python.framework.types.worker_types import WorkerRequirement, WorkerResult


class CensusProbeDecision(AbstractDecisionLogic):
    """One diagnostics row, one position opened and closed."""

    def __init__(self, name, logger, config=None, trading_context=None):
        super().__init__(name, logger, config, trading_context=trading_context)
        self._open_tick = self.params.get('open_tick')
        self._close_tick = self.params.get('close_tick')
        self._lot_size = self.params.get('lot_size')
        self._tick_count = 0
        self._wrote = False
        self._opened = False
        self._closed = False

    @classmethod
    def get_parameter_schema(cls) -> Dict[str, InputParamDef]:
        """
        The plan's three numbers.

        Returns:
            The parameter definitions
        """
        return {
            'open_tick': InputParamDef(
                param_type=int, default=20, min_val=1,
                description='Tick at which the position is opened'),
            'close_tick': InputParamDef(
                param_type=int, default=60, min_val=1,
                description='Tick at which the position is closed'),
            'lot_size': InputParamDef(
                param_type=float, default=0.001, min_val=0.0, max_val=1.0,
                description='Size of the position'),
        }

    @classmethod
    def get_required_order_types(cls, decision_logic_config: Dict[str, Any]) -> List[OrderType]:
        return [OrderType.MARKET]

    def get_required_workers(self) -> Dict[str, WorkerRequirement]:
        return {'rsi_fast': WorkerRequirement.all('CORE/rsi')}

    def on_market_data_stale(self, status: MarketDataStatus) -> None:
        """Deliberate pass: the probe's plan runs on ticks, a stale feed only delays it."""
        pass

    def compute_tick(
        self,
        tick: TickData,
        worker_results: Dict[str, WorkerResult],
    ) -> Decision:
        self._tick_count += 1
        if not self._wrote:
            self.diagnostics_csv('census_rows', ['tick_time', 'note']).append_row({
                'tick_time': tick.timestamp.isoformat(),
                'note': 'census',
            })
            self._wrote = True
        if self._tick_count == self._open_tick and not self._opened:
            return Decision(action=DecisionLogicAction.BUY, outputs={})
        return Decision(action=DecisionLogicAction.FLAT, outputs={})

    def _execute_decision_impl(
        self,
        decision: Decision,
        tick: Optional[TickData],
    ) -> Optional[OrderResult]:
        if not self.trading_api or tick is None:
            return None
        if self._tick_count >= self._close_tick and self._opened and not self._closed:
            for position in self.trading_api.get_open_positions():
                if not self.trading_api.is_pending_close(position.position_id):
                    self._closed = True
                    return self.trading_api.close_position(position.position_id)
        if decision.action == DecisionLogicAction.BUY and not self._opened:
            self._opened = True
            return self.trading_api.send_order(
                symbol=tick.symbol, order_type=OrderType.MARKET, side=OrderSide.BUY,
                lots=self._lot_size, comment='census probe open')
        return None
