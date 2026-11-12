"""
The latency simulator answers when its next pending order comes due, resolving nothing (#457).

A timeline asks this to place a resolution event; it must not move an order or read another
object's state to find out. Which orders resolve at an instant, and in which order, is decided
here too, which is why that is pinned beside it.
"""

from datetime import datetime, timezone

from python.framework.logging.global_logger import GlobalLogger
from python.framework.trading_env.simulation.order_latency_simulator import OrderLatencySimulator
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.trading_env_types.latency_simulator_types import (
    PendingOrder,
    PendingOrderAction,
    PendingOrderTiming,
)
from python.framework.types.trading_env_types.order_types import (
    OpenOrderRequest,
    OrderDirection,
    OrderType,
)

_BASE_MSC = 1_000_000


def _tick(msc: int) -> TickData:
    return TickData(
        timestamp=datetime.fromtimestamp(msc / 1000, tz=timezone.utc),
        symbol='BTCUSD', bid=50000.0, ask=50001.0, time_msc=msc, collected_msc=msc)


def _simulator() -> OrderLatencySimulator:
    return OrderLatencySimulator(
        seeds={'inbound_latency_seed': 42}, logger=GlobalLogger(name='DuePeekTest'))


def _store(simulator: OrderLatencySimulator, order_id: str, action: PendingOrderAction,
           placed_at_msc: int, due_msc: int) -> None:
    """Place an order with the timings stated, so due order and placement order can differ."""
    simulator.store_order(PendingOrder(
        pending_order_id=order_id, order_action=action,
        timing=PendingOrderTiming(placed_at_msc=placed_at_msc, broker_fill_msc=due_msc)))


class TestTheNextDueOrder:

    def test_nothing_pending_is_nothing_due(self):
        assert _simulator().peek_next_due_msc() is None

    def test_the_earliest_due_not_the_earliest_placed(self):
        """An open placed first with a long latency, a close placed later with a short one."""
        simulator = _simulator()
        _store(simulator, 'open_slow', PendingOrderAction.OPEN, _BASE_MSC, _BASE_MSC + 80)
        _store(simulator, 'close_fast', PendingOrderAction.CLOSE, _BASE_MSC + 10, _BASE_MSC + 30)

        assert simulator.peek_next_due_msc() == _BASE_MSC + 30

    def test_asking_resolves_nothing(self):
        simulator = _simulator()
        _store(simulator, 'open_slow', PendingOrderAction.OPEN, _BASE_MSC, _BASE_MSC + 80)
        _store(simulator, 'close_fast', PendingOrderAction.CLOSE, _BASE_MSC + 10, _BASE_MSC + 30)

        simulator.peek_next_due_msc()
        simulator.peek_next_due_msc()

        assert simulator.get_pending_count() == 2
        assert [order.pending_order_id for order in simulator.get_pending_orders()] == [
            'open_slow', 'close_fast'], 'asking reordered the pending orders'

    def test_the_answer_moves_on_once_an_order_resolves(self):
        simulator = _simulator()
        _store(simulator, 'open_slow', PendingOrderAction.OPEN, _BASE_MSC, _BASE_MSC + 80)
        _store(simulator, 'close_fast', PendingOrderAction.CLOSE, _BASE_MSC + 10, _BASE_MSC + 30)

        assert simulator.process_up_to_msc(_BASE_MSC + 29) == []
        resolved = simulator.process_up_to_msc(_BASE_MSC + 30)

        assert [order.pending_order_id for order in resolved] == ['close_fast']
        assert simulator.peek_next_due_msc() == _BASE_MSC + 80

    def test_an_order_sent_through_the_simulator_is_due_after_its_latency(self):
        """The seeded path end to end: a market open placed at a tick comes due within its range."""
        simulator = OrderLatencySimulator(
            seeds={'inbound_latency_seed': 42}, logger=GlobalLogger(name='DuePeekTest'),
            inbound_latency_min_ms=20, inbound_latency_max_ms=80)
        simulator.submit_open_order(
            order_id='o1',
            request=OpenOrderRequest(symbol='BTCUSD', order_type=OrderType.MARKET,
                                     direction=OrderDirection.LONG, lots=0.01),
            tick=_tick(_BASE_MSC))

        assert _BASE_MSC + 20 <= simulator.peek_next_due_msc() <= _BASE_MSC + 80


class TestOrdersDueTogetherResolveInTheOrderTheyWereSent:
    """
    Two orders due in one millisecond resolve in the order they were sent — #457's answer to the
    conflict, the way a FIX sequence number orders them — and not by their ids.
    """

    def test_the_order_sent_first_resolves_first(self):
        simulator = _simulator()
        _store(simulator, 'z_sent_first', PendingOrderAction.OPEN, _BASE_MSC, _BASE_MSC + 50)
        _store(simulator, 'a_sent_second', PendingOrderAction.CLOSE, _BASE_MSC + 5, _BASE_MSC + 50)

        resolved = simulator.process_up_to_msc(_BASE_MSC + 50)

        assert [order.pending_order_id for order in resolved] == ['z_sent_first', 'a_sent_second']
