"""
Sim/Live Parity — A Second Close Joins the Close Already in Flight

Both pipelines key an in-flight close by its position id. A second close for the same
position used to REPLACE the first: live lost the only reference to the first close's fill —
the book said open while the venue had sold — and the simulation sent the second in place of
the first. Reachable from a shipped CORE strategy: cautious_macd closes on a counter-signal
without asking whether a close is already on its way.

Now the second request joins: one order reaches the venue, the caller hears PENDING marked
`joined_in_flight_close`, and the first close keeps its own size and reason. Pinned in both
pipelines and both account models.
"""

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import pytest

from python.framework.logging.global_logger import GlobalLogger
from python.framework.testing.mock_broker_adapter import MockBrokerAdapter, MockExecutionMode
from python.framework.testing.mock_order_execution import MockOrderExecution
from python.framework.trading_env.broker_config import BrokerConfig
from python.framework.trading_env.simulation.trade_simulator import TradeSimulator
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.portfolio_types.portfolio_trade_record_types import CloseReason
from python.framework.types.trading_env_types.broker_types import BrokerType
from python.framework.types.trading_env_types.latency_simulator_types import PendingOrderAction
from python.framework.types.trading_env_types.order_types import (
    OpenOrderRequest,
    OrderAction,
    OrderDirection,
    OrderStatus,
    OrderType,
)

_SYMBOL = 'BTCUSD'
_LOTS = 0.02

ACCOUNT_MODELS = pytest.mark.parametrize('spot_mode', [False, True], ids=['margin', 'spot'])


def _balances(spot_mode: bool) -> Optional[Dict[str, float]]:
    """The spot inventory, or None at margin."""
    return {'USD': 100000.0, 'BTC': 0.0} if spot_mode else None


def _executed_closes(executor) -> List:
    """Every executed close row in the order history."""
    return [r for r in executor.get_order_history()
            if r.status == OrderStatus.EXECUTED and r.action == OrderAction.CLOSE]


class TestLiveJoins:
    """Live: the second request never reaches the venue."""

    @ACCOUNT_MODELS
    def test_one_close_reaches_the_venue_and_the_book_follows_it(self, spot_mode):
        mock = MockOrderExecution(
            mode=MockExecutionMode.DELAYED_FILL, initial_balance=100000.0,
            spot_mode=spot_mode, initial_balances=_balances(spot_mode))
        executor = mock.create_executor()
        adapter = executor.broker.adapter
        submits: List[OrderDirection] = []
        original_submit = adapter.do_request_submit

        def counting_submit(payload):
            submits.append(payload['direction'])
            return original_submit(payload)

        adapter.do_request_submit = counting_submit
        mock.feed_tick(executor)
        executor.open_order(OpenOrderRequest(
            symbol=_SYMBOL, order_type=OrderType.MARKET,
            direction=OrderDirection.LONG, lots=_LOTS))
        mock.feed_tick(executor)
        mock.feed_tick(executor)
        position_id = executor.get_open_positions()[0].position_id

        first = executor.close_position(position_id)
        second = executor.close_position(
            position_id, lots=0.01, close_reason=CloseReason.SL_TRIGGERED)

        assert first.status == second.status == OrderStatus.PENDING
        assert second.metadata.get('joined_in_flight_close') is True
        closes = executor.get_request_processor().get_pending_orders(PendingOrderAction.CLOSE)
        assert len(closes) == 1
        assert closes[0].close_lots == pytest.approx(_LOTS), 'the first close was a full close'
        assert closes[0].close_reason == CloseReason.MANUAL

        mock.feed_tick(executor)
        mock.feed_tick(executor)

        assert submits.count(OrderDirection.SHORT) == 1, 'the second close reached the venue'
        assert not executor.get_open_positions()
        assert len(_executed_closes(executor)) == 1


class TestSimulationJoins:
    """Simulation: the second request does not replace the first in the latency queue."""

    @ACCOUNT_MODELS
    def test_the_first_close_keeps_its_timing_size_and_reason(self, spot_mode):
        sim = TradeSimulator(
            broker_config=BrokerConfig(
                BrokerType.KRAKEN_SPOT, MockBrokerAdapter(mode=MockExecutionMode.INSTANT_FILL)),
            initial_balance=100000.0, account_currency='USD',
            logger=GlobalLogger('CloseInFlightParity'),
            seeds={'inbound_latency_seed': 42},
            inbound_latency_min_ms=1000, inbound_latency_max_ms=1000,
            spot_mode=spot_mode, initial_balances=_balances(spot_mode))
        start = datetime(2026, 1, 5, 12, 0, 0, tzinfo=timezone.utc)

        def tick_at(seconds: float) -> None:
            moment = start + timedelta(seconds=seconds)
            msc = int(moment.timestamp() * 1000)
            sim.on_tick(TickData(timestamp=moment, symbol=_SYMBOL, bid=50000.0, ask=50001.0,
                                 time_msc=msc, collected_msc=msc))

        tick_at(0)
        sim.open_order(OpenOrderRequest(
            symbol=_SYMBOL, order_type=OrderType.MARKET,
            direction=OrderDirection.LONG, lots=_LOTS))
        tick_at(2)
        position_id = sim.get_open_positions()[0].position_id
        tick_at(3)
        sim.close_position(position_id, close_reason=CloseReason.MANUAL)
        first = sim.latency_simulator.get_order(position_id)
        fill_msc = first.timing.broker_fill_msc
        tick_at(3.5)

        second = sim.close_position(
            position_id, lots=0.01, close_reason=CloseReason.SL_TRIGGERED)

        assert second.status == OrderStatus.PENDING
        assert second.metadata.get('joined_in_flight_close') is True
        in_flight = sim.latency_simulator.get_order(position_id)
        assert in_flight is first, 'the second close replaced the first'
        assert in_flight.timing.broker_fill_msc == fill_msc
        assert in_flight.close_lots is None and in_flight.close_reason == CloseReason.MANUAL

        tick_at(4.2)
        tick_at(5)

        assert not sim.get_open_positions()
        assert len(_executed_closes(sim)) == 1
