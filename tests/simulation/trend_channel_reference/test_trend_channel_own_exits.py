"""
Trend Channel Reference — its own exits and its entries, driven tick by tick (#625).

The logic keeps its stop and target itself and trails the stop toward the market. The backtest
windows of this suite show the trail only where a trailed stop was the exit, and in them the
trailed positions ran on into their targets. So the trail is driven here directly: levels and
initial risk handed in through the logic's own restore hook, three ticks through its execution
pass, the stop read back through its snapshot — the public surface a restart uses.

The same pass tracks the one entry it allows at a time. An entry that ended without leaving a
position — refused, cancelled, or filled and closed before the pass saw it — has to let go of
that slot, or the logic never trades again; one that is still on its way must not.
"""

from datetime import datetime, timezone
from typing import List, Optional

from python.framework.decision_logic.core.trend_channel_reference import TrendChannelReference
from python.framework.factory.decision_logic_factory import DecisionLogicFactory
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.types.decision_logic_types import Decision, DecisionLogicAction
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.portfolio_types.portfolio_trade_record_types import CloseReason
from python.framework.types.portfolio_types.portfolio_types import Position
from python.framework.types.trading_env_types.order_types import (
    OrderAction,
    OrderDirection,
    OrderResult,
    OrderStatus,
    OrderType,
)
from python.framework.types.trading_env_types.own_exit_types import OwnExit

_T0 = datetime(2026, 1, 5, 9, 0, tzinfo=timezone.utc)


class _FakeTradingApi:
    """What the execution pass asks while it manages positions and places one entry."""

    def __init__(self, positions: List[Position]):
        self.positions = positions
        self.closes: List[tuple] = []
        self.sent: List[OrderType] = []
        self.pipeline_busy = False

    def get_current_time(self) -> datetime:
        return _T0

    def has_pipeline_orders(self) -> bool:
        return self.pipeline_busy

    def is_spot_mode(self) -> bool:
        return False

    def get_free_entry_capital(self, symbol: str, direction: OrderDirection) -> float:
        return 1e9

    def send_order(self, order_type: OrderType, **kwargs) -> OrderResult:
        self.sent.append(order_type)
        return OrderResult(order_id=f'ord_{len(self.sent)}', status=OrderStatus.PENDING,
                           action=OrderAction.OPEN)

    def cancel_limit_order(self, order_id: str) -> bool:
        return False

    def get_open_positions(self, symbol: Optional[str] = None) -> List[Position]:
        return list(self.positions)

    def get_position(self, position_id: str) -> Optional[Position]:
        return None

    def get_active_orders(self) -> list:
        return []

    def is_pending_close(self, position_id: str) -> bool:
        return any(closed == position_id for closed, _ in self.closes)

    def has_in_flight_operation(self, order_id: str) -> bool:
        return False

    def close_position(self, position_id: str, lots=None, exit=None) -> OrderResult:
        self.closes.append((position_id, exit))
        return OrderResult(order_id=position_id, status=OrderStatus.PENDING,
                           action=OrderAction.CLOSE)


def _logic(tmp_path) -> TrendChannelReference:
    """
    The logic at its defaults, with a trail of one initial risk behind the price.

    Args:
        tmp_path: Where its log goes

    Returns:
        The logic
    """
    logger = ScenarioLogger('trend_channel_own_exits', 'trend_channel_own_exits', _T0,
                            'run_trend_channel_own_exits', log_root_override=tmp_path)
    # Through the factory, which fills the schema's defaults in
    return DecisionLogicFactory(logger).create_logic(
        logic_type='CORE/trend_channel_reference', logger=logger,
        logic_config={'trail_mult': 1.0})


def _flat(price: float) -> Decision:
    return Decision(action=DecisionLogicAction.FLAT, outputs={'reason': 'manage', 'price': price})


def _tick(bid: float) -> TickData:
    return TickData(timestamp=_T0, symbol='EURUSD', bid=bid, ask=bid + 0.1)


def _buy() -> Decision:
    return Decision(action=DecisionLogicAction.BUY, outputs={
        'reason': 'armed', 'gate': 'up', 'entry_price': 101.0, 'stop_loss': 99.0,
        'take_profit': 105.0, 'band_width': 4.0})


def test_the_trail_moves_the_own_stop_past_the_entry_and_the_stop_closes(tmp_path):
    logic = _logic(tmp_path)
    position = Position(position_id='pos_1', symbol='EURUSD', direction=OrderDirection.LONG,
                        lots=0.1, original_lots=0.1, entry_price=100.0, entry_time=_T0)
    api = _FakeTradingApi([position])
    logic.set_trading_api(api)
    logic.restore_state({
        'own_exit_levels': {'pos_1': {'stop': 95.0, 'target': 120.0}},
        'initial_risk': {'pos_1': 5.0},
        'partial_done': ['pos_1'],      # keeps the partial-close rung out of the way
    })

    # Price 108: the stop trails to one initial risk behind it, past the entry
    logic.execute_decision(_flat(108.0), _tick(108.0))
    stop = logic.get_state_snapshot()['own_exit_levels']['pos_1']['stop']
    assert stop == 103.0

    # Price 104: a trail that only tightens stays at 103
    logic.execute_decision(_flat(104.0), _tick(104.0))
    assert logic.get_state_snapshot()['own_exit_levels']['pos_1']['stop'] == 103.0
    assert api.closes == []

    # Price 102.9: the bid reached the trailed stop — the logic closes, naming it
    logic.execute_decision(_flat(102.9), _tick(102.9))
    assert api.closes == [('pos_1', OwnExit(CloseReason.SL_TRIGGERED, 103.0))]


def test_a_restore_takes_back_only_the_positions_the_framework_restored(tmp_path):
    logic = _logic(tmp_path)
    position = Position(position_id='pos_1', symbol='EURUSD', direction=OrderDirection.LONG,
                        lots=0.1, original_lots=0.1, entry_price=100.0, entry_time=_T0)
    logic.set_trading_api(_FakeTradingApi([position]))

    logic.restore_state({
        'own_exit_levels': {'pos_1': {'stop': 99.0, 'target': None},
                            'pos_9': {'stop': 50.0, 'target': None}},
        'initial_risk': {'pos_1': 1.0, 'pos_9': 2.0},
        'partial_done': ['pos_1', 'pos_9'],
    })

    snapshot = logic.get_state_snapshot()
    assert set(snapshot['own_exit_levels']) == {'pos_1'}
    assert snapshot['initial_risk'] == {'pos_1': 1.0}
    assert snapshot['partial_done'] == ['pos_1']


def test_an_entry_that_ended_without_a_position_frees_the_slot(tmp_path):
    """Neither a position nor resting nor on its way — the entry is over, and a new one may go.
    Kept tracked, it blocked every later entry: a backtest stopped trading for its last four
    and a half days after a stop fired on the entry's own fill tick."""
    logic = _logic(tmp_path)
    api = _FakeTradingApi([])
    logic.set_trading_api(api)

    logic.execute_decision(_buy(), _tick(100.0))
    assert api.sent == [OrderType.LIMIT]

    # The entry is gone from every place an order can be — the next armed setup goes out
    logic.execute_decision(_buy(), _tick(100.0))
    assert api.sent == [OrderType.LIMIT, OrderType.LIMIT]


def test_an_entry_still_on_its_way_keeps_the_slot(tmp_path):
    """In the submission pipeline an order is neither resting nor a position yet."""
    logic = _logic(tmp_path)
    api = _FakeTradingApi([])
    logic.set_trading_api(api)

    logic.execute_decision(_buy(), _tick(100.0))
    api.pipeline_busy = True
    logic.execute_decision(_buy(), _tick(100.0))

    assert api.sent == [OrderType.LIMIT]
