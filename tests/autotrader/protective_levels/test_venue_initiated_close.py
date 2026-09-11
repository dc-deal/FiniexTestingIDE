"""
FiniexTestingIDE - Venue-Initiated Close (#503, stage B)

Everything behind `_fill_close_order` was written on one assumption: that a close was
REQUESTED here. A protective order the venue holds breaks it — the venue decides, and the
fill arrives with no local request behind it. This is the first time the exchange writes
into our position book.

Two consequences are pinned here. A resting order that fills is no longer necessarily an
ENTRY: routed the old way, a firing stop would open a SECOND position, book an entry fee
and tell the algo an order had filled. And a close order can now carry its own id, so the
position it settles has to be named separately — `pending_order_id` is the wire key's
source and overloading it would collide with a restart's counter.

The POSITION_CLOSED event is deliberately not narrowed to the venue case: it fires on every
full close in BOTH pipelines. A live-only event is a parity break of the same family the
external-data contract forbids, and a strategy reacting to "my position is gone" needs it
whichever route closed it.
"""

from datetime import datetime, timezone

from python.framework.logging.global_logger import GlobalLogger
from python.framework.testing.mock_broker_adapter import MockBrokerAdapter, MockExecutionMode
from python.framework.testing.mock_order_execution import MockOrderExecution
from python.framework.trading_env.broker_config import BrokerConfig
from python.framework.trading_env.simulation.trade_simulator import TradeSimulator
from python.framework.types.decision_event_types import (
    PartialCloseEvent,
    PositionClosedEvent,
)
from python.framework.types.live_types.live_execution_types import (
    BrokerOrderStatus,
    BrokerResponse,
)
from python.framework.types.live_types.live_request_types import QueryResponse
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.portfolio_types.portfolio_trade_record_types import CloseReason
from python.framework.types.trading_env_types.broker_types import BrokerType
from python.framework.types.trading_env_types.latency_simulator_types import (
    PendingOrder,
    PendingOrderAction,
)
from python.framework.types.trading_env_types.order_types import (
    OpenOrderRequest,
    OrderDirection,
    OrderType,
)

_SYMBOL = 'BTCUSD'


def _live_with_one_long():
    """
    A live executor holding one filled LONG of 0.01 lots.

    Returns:
        (mock, executor, position) — the harness, the executor and the open position
    """
    mock = MockOrderExecution(mode=MockExecutionMode.INSTANT_FILL)
    executor = mock.create_executor()
    mock.feed_tick(executor, symbol=_SYMBOL, bid=50000.0, ask=50001.0)
    executor.open_order(OpenOrderRequest(
        symbol=_SYMBOL, order_type=OrderType.MARKET, direction=OrderDirection.LONG,
        lots=0.01, stop_loss=49000.0))
    mock.feed_tick(executor, symbol=_SYMBOL, bid=50000.0, ask=50001.0)
    return mock, executor, executor.get_open_positions()[0]


def _protective_order(position_id: str, order_id: str = 'ord_protect_1') -> PendingOrder:
    """
    The shape a venue-held protective order arrives in.

    Its id is minted from the order counter like any other order — the wire key is derived
    from it — so it names its position in `closes_position_id` instead of BEING it.

    Args:
        position_id: The position this order protects
        order_id: The order's own id

    Returns:
        A CLOSE PendingOrder ready to be routed as filled
    """
    return PendingOrder(
        pending_order_id=order_id,
        order_action=PendingOrderAction.CLOSE,
        order_type=OrderType.STOP,
        broker_ref='OABCDE-1234-XYZ',
        symbol=_SYMBOL,
        direction=OrderDirection.SHORT,
        close_lots=0.01,
        close_reason=CloseReason.SL_TRIGGERED,
        closes_position_id=position_id,
    )


class TestAFiringStopDoesNotOpenAPosition:
    """
    The routing line. Three of the four live fill sites called `_fill_open_order`
    unconditionally, which held only as long as every resting order was an entry.
    """

    def test_a_filled_close_order_closes_and_opens_nothing(self):
        mock, executor, position = _live_with_one_long()
        before = len(executor.get_trade_history())

        executor._route_resting_fill(
            _protective_order(position.position_id), fill_price=49000.0)

        assert not executor.get_open_positions(), (
            'The protective order fired; the position it protected has to be gone — '
            'routed as an OPEN it would have added a SECOND position beside it')
        assert len(executor.get_trade_history()) == before + 1, (
            'Exactly one closing trade record')

    def test_the_close_is_booked_against_the_named_position(self):
        mock, executor, position = _live_with_one_long()

        executor._route_resting_fill(
            _protective_order(position.position_id), fill_price=49000.0)

        record = executor.get_trade_history()[-1]
        assert record.position_id == position.position_id
        assert record.close_reason == CloseReason.SL_TRIGGERED

    def test_a_locally_requested_close_still_resolves_by_its_own_id(self):
        """
        The unchanged case, and the reason the resolution is `closes_position_id OR
        pending_order_id`: every close the strategy or the engine requests carries the
        position's id AS the order id, and that must keep working untouched.
        """
        mock, executor, position = _live_with_one_long()

        executor.close_position(position.position_id)
        mock.feed_tick(executor, symbol=_SYMBOL, bid=50000.0, ask=50001.0)
        mock.feed_tick(executor, symbol=_SYMBOL, bid=50000.0, ask=50001.0)

        assert not executor.get_open_positions()
        assert executor.get_trade_history()[-1].position_id == position.position_id


class TestPositionClosedReachesTheAlgo:
    """
    Until now a FULL close emitted nothing — the algo learned of it by noticing the
    position missing from `get_open_positions()`. That reading breaks the moment the venue
    can close a position on its own.
    """

    def test_a_full_close_emits_it_once(self):
        mock, executor, position = _live_with_one_long()
        events = []
        executor.set_decision_event_sink(events.append)

        executor.close_position(position.position_id)
        mock.feed_tick(executor, symbol=_SYMBOL, bid=50000.0, ask=50001.0)
        mock.feed_tick(executor, symbol=_SYMBOL, bid=50000.0, ask=50001.0)

        closed = [e for e in events if isinstance(e, PositionClosedEvent)]
        assert len(closed) == 1
        assert closed[0].position_id == position.position_id
        assert closed[0].requested_locally

    def test_a_venue_initiated_close_says_nobody_here_asked(self):
        mock, executor, position = _live_with_one_long()
        events = []
        executor.set_decision_event_sink(events.append)

        executor._route_resting_fill(
            _protective_order(position.position_id), fill_price=49000.0)

        closed = [e for e in events if isinstance(e, PositionClosedEvent)]
        assert len(closed) == 1
        assert not closed[0].requested_locally, (
            'The venue fired its own order — no local close is behind this fill')
        assert closed[0].close_reason == CloseReason.SL_TRIGGERED

    def test_a_partial_close_emits_the_partial_event_and_not_this_one(self):
        mock = MockOrderExecution(mode=MockExecutionMode.INSTANT_FILL)
        executor = mock.create_executor()
        mock.feed_tick(executor, symbol=_SYMBOL, bid=50000.0, ask=50001.0)
        executor.open_order(OpenOrderRequest(
            symbol=_SYMBOL, order_type=OrderType.MARKET, direction=OrderDirection.LONG,
            lots=0.10))
        mock.feed_tick(executor, symbol=_SYMBOL, bid=50000.0, ask=50001.0)
        position = executor.get_open_positions()[0]
        events = []
        executor.set_decision_event_sink(events.append)

        executor.close_position(position.position_id, lots=0.04)
        mock.feed_tick(executor, symbol=_SYMBOL, bid=50000.0, ask=50001.0)
        mock.feed_tick(executor, symbol=_SYMBOL, bid=50000.0, ask=50001.0)

        assert any(isinstance(e, PartialCloseEvent) for e in events)
        assert not any(isinstance(e, PositionClosedEvent) for e in events), (
            'Lots remain open; the position did not close')

    def test_the_simulation_emits_it_too(self):
        """
        Parity, and it is the point rather than a bonus: an event only live can produce
        would make a backtest stop predicting the live run.
        """
        simulator = TradeSimulator(
            broker_config=BrokerConfig(
                BrokerType.KRAKEN_SPOT,
                MockBrokerAdapter(mode=MockExecutionMode.INSTANT_FILL)),
            initial_balance=100000.0,
            account_currency='USD',
            logger=GlobalLogger('VenueInitiatedCloseSim'),
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
                simulator.close_position(
                    simulator.get_open_positions()[0].position_id)

        closed = [e for e in events if isinstance(e, PositionClosedEvent)]
        assert len(closed) == 1, f'{len(closed)} POSITION_CLOSED events in the backtest'
        assert closed[0].requested_locally


class TestTheQueryPathRoutesAVenueStopToTheCloseHalf:
    """
    The one fill site a firing venue stop actually takes, driven through the REAL handler
    rather than through the router.

    The other three sites carry ENTRIES; this is the one a protective order reaches, and
    it is where the old unconditional `_fill_open_order` would have opened a second
    position with real money. Placement is stage D's, so the order is put into the active
    list here by hand — that is exactly the state stage D will produce, and it is no more
    constructed than the QueryResponse the test hands in beside it.
    """

    def test_a_filled_answer_closes_the_position_instead_of_opening_one(self):
        mock, executor, position = _live_with_one_long()
        protective = _protective_order(position.position_id)
        executor._active_stop_orders.append(protective)

        executor._handle_query_response(QueryResponse(
            order_id=protective.pending_order_id,
            broker_response=BrokerResponse(
                broker_ref=protective.broker_ref,
                status=BrokerOrderStatus.FILLED,
                fill_price=49000.0,
                filled_lots=0.01,
                timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
            ),
        ))

        assert not executor.get_open_positions(), (
            'The venue fired the stop — the position it protected has to be gone')
        assert protective not in executor._active_stop_orders
        assert executor.get_trade_history()[-1].close_reason == CloseReason.SL_TRIGGERED
