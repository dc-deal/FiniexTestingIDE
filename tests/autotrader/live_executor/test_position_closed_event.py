"""
The Position-Closed Decision Event

`on_position_closed` is the hook a logic learns of a full close by, whichever route closed it. It
fires in both pipelines: an event only live can produce would make a backtest stop predicting a
session.
"""

from datetime import datetime, timezone

from python.framework.logging.global_logger import GlobalLogger
from python.framework.testing.mock_broker_adapter import MockBrokerAdapter, MockExecutionMode
from python.framework.testing.mock_order_execution import MockOrderExecution
from python.framework.trading_env.broker_config import BrokerConfig
from python.framework.trading_env.simulation.trade_simulator import TradeSimulator
from python.framework.types.decision_event_types import PartialCloseEvent, PositionClosedEvent
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.trading_env_types.broker_types import BrokerType
from python.framework.types.trading_env_types.order_types import (
    OpenOrderRequest,
    OrderDirection,
    OrderType,
)

_SYMBOL = 'BTCUSD'


def _live_with_one_long() -> tuple:
    """
    A live executor holding one LONG.

    Returns:
        (mock, executor, position, the list its decision events land in)
    """
    mock = MockOrderExecution(mode=MockExecutionMode.INSTANT_FILL)
    executor = mock.create_executor()
    mock.feed_tick(executor, symbol=_SYMBOL, bid=50000.0, ask=50001.0)
    executor.open_order(OpenOrderRequest(
        symbol=_SYMBOL, order_type=OrderType.MARKET, direction=OrderDirection.LONG, lots=0.10))
    mock.feed_tick(executor, symbol=_SYMBOL, bid=50000.0, ask=50001.0)
    events = []
    executor.set_decision_event_sink(events.append)
    return mock, executor, executor.get_open_positions()[0], events


class TestAFullCloseReachesTheLogic:

    def test_a_full_close_emits_it_once(self):
        mock, executor, position, events = _live_with_one_long()

        executor.close_position(position.position_id)
        mock.feed_tick(executor, symbol=_SYMBOL, bid=50000.0, ask=50001.0)
        mock.feed_tick(executor, symbol=_SYMBOL, bid=50000.0, ask=50001.0)

        closed = [event for event in events if isinstance(event, PositionClosedEvent)]
        assert len(closed) == 1
        assert closed[0].position_id == position.position_id

    def test_a_partial_close_emits_the_partial_event_and_not_this_one(self):
        mock, executor, position, events = _live_with_one_long()

        executor.close_position(position.position_id, lots=0.04)
        mock.feed_tick(executor, symbol=_SYMBOL, bid=50000.0, ask=50001.0)
        mock.feed_tick(executor, symbol=_SYMBOL, bid=50000.0, ask=50001.0)

        assert any(isinstance(event, PartialCloseEvent) for event in events)
        assert not any(isinstance(event, PositionClosedEvent) for event in events), (
            'lots remain open: the position did not close')

    def test_the_simulation_emits_it_too(self):
        simulator = TradeSimulator(
            broker_config=BrokerConfig(
                BrokerType.KRAKEN_SPOT, MockBrokerAdapter(mode=MockExecutionMode.INSTANT_FILL)),
            initial_balance=100000.0,
            account_currency='USD',
            logger=GlobalLogger('PositionClosedEvent'),
            seeds={'inbound_latency_seed': 42},
            inbound_latency_min_ms=0,
            inbound_latency_max_ms=0,
        )
        events = []
        simulator.set_decision_event_sink(events.append)

        for step in range(4):
            simulator.on_tick(TickData(
                timestamp=datetime(2026, 1, 1, 0, 0, step, tzinfo=timezone.utc),
                symbol=_SYMBOL, bid=50000.0, ask=50001.0, volume=1.0))
            if step == 0:
                simulator.open_order(OpenOrderRequest(
                    symbol=_SYMBOL, order_type=OrderType.MARKET,
                    direction=OrderDirection.LONG, lots=0.01))
            if step == 2:
                simulator.close_position(simulator.get_open_positions()[0].position_id)

        closed = [event for event in events if isinstance(event, PositionClosedEvent)]
        assert len(closed) == 1, f'{len(closed)} position-closed events in the backtest'
