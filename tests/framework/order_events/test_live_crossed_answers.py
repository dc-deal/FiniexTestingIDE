"""
FiniexTestingIDE - Order Events When Answers Cross (#362)

Live, an answer can arrive after another one has already settled its question: a fill read
before a cancel's refusal, the truth pull naming an order while the resolution's own question is
still on its way, a cancel asked for twice before the venue named the order. Each request is
still answered once in the stream, the way the simulation answers it. And a cancel the framework
sent is the framework's in the record, whatever the venue reports afterwards.
"""

import time
from datetime import datetime, timedelta, timezone

import pytest

from python.framework.exceptions.connection_errors import ConnectionAttemptFailedError
from python.framework.logging.global_logger import GlobalLogger
from python.framework.testing.mock_broker_adapter import MockExecutionMode
from python.framework.testing.mock_order_execution import MockOrderExecution
from python.framework.trading_env.broker_config import BrokerConfig
from python.framework.trading_env.live.live_trade_executor import LiveTradeExecutor
from python.framework.types.live_types.live_execution_types import (
    BrokerOrderStatus,
    BrokerResponse,
    TimeoutConfig,
)
from python.framework.types.live_types.live_request_types import (
    OrderResolveResponse,
    QueryResponse,
)
from python.framework.types.live_types.reconciliation_types import BrokerOrder
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.trading_env_types.broker_types import BrokerType
from python.framework.types.trading_env_types.order_event_types import (
    OrderEventType,
    OrderOperation,
)
from python.framework.types.trading_env_types.order_types import (
    OpenOrderRequest,
    OrderDirection,
    OrderEndReason,
    OrderInitiator,
    OrderStatus,
    OrderType,
)
from tests.autotrader.live_executor.conftest import AcknowledgingVenueMock
from tests.autotrader.protective_levels.conftest import VenueHoldsProtectionMock
from tests.framework.order_events.conftest import (
    limit_order,
    live_session,
    order_lives,
    order_steps,
    record_events,
)

ACCOUNT_MODELS = pytest.mark.parametrize('spot_mode', [False, True], ids=['margin', 'spot'])
_FAULT = 'venue unreachable'


def _timeout_executor(venue: AcknowledgingVenueMock, spot_mode: bool) -> LiveTradeExecutor:
    """
    A live executor on the acknowledging venue, whose market orders the venue takes and holds.

    Args:
        venue: The venue mock
        spot_mode: The account model

    Returns:
        The executor
    """
    executor = LiveTradeExecutor(
        broker_config=BrokerConfig(BrokerType.KRAKEN_SPOT, venue),
        initial_balance=100000.0,
        account_currency='USD',
        logger=GlobalLogger(name='CrossedAnswers'),
        timeout_config=TimeoutConfig(order_timeout_seconds=30.0),
        spot_mode=spot_mode,
        initial_balances={'USD': 100000.0, 'BTC': 0.0} if spot_mode else None,
        session_key='xans',
    )
    executor.get_request_processor().flush_outbox()
    executor.on_tick(TickData(
        timestamp=datetime.now(timezone.utc), symbol='BTCUSD', bid=50000.0, ask=50001.0))
    return executor


def _acknowledged_market(executor: LiveTradeExecutor) -> str:
    """
    A market BUY the venue answered with its reference and did not fill.

    Args:
        executor: The executor

    Returns:
        The order id
    """
    order_id = executor.open_order(OpenOrderRequest(
        symbol='BTCUSD', order_type=OrderType.MARKET,
        direction=OrderDirection.LONG, lots=0.01)).order_id
    processor = executor.get_request_processor()
    processor.flush_outbox()
    processor.drain_inbox()
    return order_id


def _time_out(executor: LiveTradeExecutor, order_id: str) -> None:
    """Run the fill timeout of one order, as a heartbeat after its deadline passed."""
    pending = executor.get_request_processor().get_order(order_id)
    pending.timing.order_timeout_deadline_monotonic = time.monotonic() - 1.0
    executor.get_request_processor().flush_outbox()
    executor.set_current_time(datetime.now(timezone.utc))
    executor.heartbeat()


class _LosesTheCancelAnswer(AcknowledgingVenueMock):
    """A venue that carries the cancel out and whose answer is then lost on the way back."""

    def do_request_cancel(self, payload):
        super().do_request_cancel(payload)
        raise ConnectionAttemptFailedError('read timeout after the cancel', terminal=False)


class TestAFillOvertakesARequest:
    """The fill is read before the refusal of the request it overtook — refused, then filled."""

    @pytest.mark.parametrize('request_kind, refused', [
        ('cancel', 'cancel_rejected'), ('modify', 'modify_rejected')])
    def test_the_request_is_refused_before_the_fill(self, request_kind, refused):
        mock, executor, events = live_session()
        order_id = executor.open_order(limit_order()).order_id
        mock.await_submit_confirmation(executor)
        executor._process_active_orders()                     # a poll goes out first
        if request_kind == 'cancel':
            assert executor.cancel_limit_order(order_id)
        else:
            assert executor.modify_limit_order(order_id, new_price=41000.0).success
        mock.await_submit_confirmation(executor)              # its FILLED is read first

        requested = 'cancel_requested' if request_kind == 'cancel' else 'modify_requested'
        assert order_steps(events) == [['submitted', 'accepted', requested, refused, 'filled']]


class TestALateResolveAnswer:
    """The truth pull settled the question while the resolution's own was still out."""

    def test_the_answer_after_the_settlement_writes_nothing(self):
        mock, executor, events = live_session(MockExecutionMode.INSTANT_FILL)
        adapter = executor.broker.adapter
        adapter.set_transport_fault('submit', _FAULT)
        executor.open_order(limit_order())
        pending = executor.get_active_orders()[0]
        mock.await_submit_confirmation(executor)
        executor.heartbeat()                                  # the resolution is armed
        adapter.set_transport_fault('submit', None)
        venue_order = BrokerOrder(
            broker_ref='MOCK-RECOVERED', symbol='BTCUSD', direction=OrderDirection.LONG,
            order_type=OrderType.LIMIT, lots=0.01, status=BrokerOrderStatus.PENDING,
            price=40000.0, client_order_id=pending.client_order_id)
        adapter.set_broker_orders([venue_order])
        pending.execution_state.resolution_next_at = (
            executor.get_current_time() - timedelta(seconds=1))
        executor._process_unresolved_orders()                 # the question goes out
        executor.get_request_processor().flush_outbox()       # answered, not yet read
        executor.apply_order_attributions([(pending, venue_order)])

        executor.get_request_processor().drain_inbox()        # the answer arrives late

        assert order_steps(events) == [['submitted', 'unresolved', 'resolved', 'accepted']]


class TestACancelBeforeTheReference:
    """However often it is asked for, one cancel is parked and one goes out."""

    def test_asked_twice_it_is_parked_once(self):
        mock, executor, events = live_session()
        order_id = executor.open_order(limit_order()).order_id

        assert executor.cancel_limit_order(order_id)
        assert executor.cancel_limit_order(order_id)
        mock.await_submit_confirmation(executor)              # the answer, then the cancel
        mock.await_submit_confirmation(executor)              # the cancel's answer

        assert order_steps(events) == [[
            'submitted', 'cancel_deferred', 'accepted', 'cancel_requested', 'cancelled']]


class TestACancelTheVenueDidNotCarryOut:
    """Its answer was lost and the venue still works the order: a later end is the venue's."""

    def test_a_later_expiry_is_the_venues(self):
        mock, executor, events = live_session(MockExecutionMode.TIMEOUT)
        order_id = executor.open_order(limit_order()).order_id
        mock.await_submit_confirmation(executor)
        pending = executor.get_active_orders()[0]
        executor.broker.adapter.set_transport_fault('cancel', _FAULT)
        assert executor.cancel_limit_order(order_id)
        mock.await_submit_confirmation(executor)              # the cancel's answer is lost
        executor._resolve_order_is_named(pending, OrderResolveResponse(
            order_id=order_id, status=BrokerOrderStatus.PENDING,
            broker_ref=pending.broker_ref))

        executor._handle_query_response(QueryResponse(
            order_id=order_id,
            broker_response=BrokerResponse(
                broker_ref=pending.broker_ref, status=BrokerOrderStatus.EXPIRED,
                timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc))))

        ending = events[-1]
        assert (ending.event_type, ending.initiator, ending.end_reason) == (
            OrderEventType.EXPIRED, OrderInitiator.VENUE, OrderEndReason.VENUE_EXPIRED)


class TestTheFillTimeoutsOwnCancel:
    """The framework cancels an order nobody filled; the record says it was the framework."""

    @ACCOUNT_MODELS
    def test_after_a_partial_fill_the_ending_is_the_frameworks(self, spot_mode):
        venue = AcknowledgingVenueMock()
        venue.fill_market_orders = False
        executor = _timeout_executor(venue, spot_mode)
        events = record_events(executor)
        order_id = _acknowledged_market(executor)
        ref = executor.get_request_processor().get_order(order_id).broker_ref
        venue.set_venue_status(ref, 'PENDING', filled_lots=0.004, fill_price=50001.0)

        _time_out(executor, order_id)

        assert order_steps(events) == [[
            'submitted', 'accepted', 'cancel_requested', 'partially_filled', 'cancelled']]
        ending = events[-1]
        assert (ending.initiator, ending.end_reason, ending.cum_lots) == (
            OrderInitiator.FRAMEWORK, OrderEndReason.ORDER_TIMEOUT, pytest.approx(0.004))

    def test_a_lost_cancel_answer_is_the_cancels_question(self):
        venue = _LosesTheCancelAnswer()
        venue.fill_market_orders = False
        executor = _timeout_executor(venue, spot_mode=False)
        events = record_events(executor)
        order_id = _acknowledged_market(executor)

        _time_out(executor, order_id)

        unresolved = [e for e in events if e.event_type is OrderEventType.UNRESOLVED]
        assert [e.lost_request for e in unresolved] == [OrderOperation.CANCEL], (
            'the read answered; the answer lost was the cancel\'s')

    def test_the_give_up_keeps_its_cancels_refusal(self):
        venue = AcknowledgingVenueMock()                      # it fills the order at once
        executor = _timeout_executor(venue, spot_mode=False)
        events = record_events(executor)
        order_id = _acknowledged_market(executor)
        venue.query_fault = ConnectionAttemptFailedError('HTTP 502', terminal=False)
        _time_out(executor, order_id)                         # handed to the resolution
        pending = executor.get_request_processor().get_order(order_id)
        executor._resolve_order_is_named(pending, OrderResolveResponse(
            order_id=order_id, status=BrokerOrderStatus.PENDING, broker_ref=pending.broker_ref))

        _time_out(executor, order_id)                         # given up

        refused = [e for e in events if e.event_type is OrderEventType.CANCEL_REJECTED]
        assert len(refused) == 1 and refused[0].venue_reason
        assert events[-1].event_type is OrderEventType.UNACCOUNTED


class TestAProtectiveOrderPartlyFilled:
    """What executed while it worked is its ending; the cancel that follows writes no second row."""

    def test_the_cancel_answer_ends_it_as_the_fill_it_already_was(self):
        mock = MockOrderExecution(mode=MockExecutionMode.INSTANT_FILL)
        executor = LiveTradeExecutor(
            broker_config=BrokerConfig(
                BrokerType.KRAKEN_SPOT,
                VenueHoldsProtectionMock(mode=MockExecutionMode.INSTANT_FILL)),
            initial_balance=100000.0,
            account_currency='USD',
            logger=GlobalLogger('CrossedAnswersProtective'),
            venue_held_protection=True,
        )
        mock.feed_tick(executor, bid=50000.0, ask=50001.0)
        events = record_events(executor)
        executor.open_order(OpenOrderRequest(
            symbol='BTCUSD', order_type=OrderType.MARKET, direction=OrderDirection.LONG,
            lots=0.1, stop_loss=49000.0))
        mock.await_submit_confirmation(executor)              # the entry fills
        mock.await_submit_confirmation(executor)              # the venue takes the stop
        position = executor.get_open_positions()[0]
        stop = [p for p in executor.get_active_orders() if p.closes_position_id][0]
        executor._handle_query_response(QueryResponse(
            order_id=stop.pending_order_id,
            broker_response=BrokerResponse(
                broker_ref=stop.broker_ref, status=BrokerOrderStatus.PENDING,
                filled_lots=0.04, fill_price=49000.0,
                timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc))))

        executor.close_position(position.position_id)
        mock.await_submit_confirmation(executor)              # the stop's cancel
        mock.await_submit_confirmation(executor)              # the close

        stop_steps = order_lives(events)[stop.submitted_seq]
        assert stop_steps == [
            'submitted', 'accepted', 'partially_filled', 'cancel_requested', 'cancelled']
        ending = [e for e in events if e.submitted_seq == stop.submitted_seq][-1]
        assert (ending.lots, ending.cum_lots) == (None, pytest.approx(0.04))
        stop_rows = [r for r in executor.get_order_history()
                     if r.order_id == stop.pending_order_id]
        assert [r.status for r in stop_rows] == [OrderStatus.EXECUTED]


class TestTheSessionEnd:
    """A session-end cancel whose answer was lost is a question, then the order is unaccounted."""

    def test_the_lost_answer_is_recorded_before_the_ending(self):
        mock, executor, events = live_session(MockExecutionMode.TIMEOUT)
        executor.open_order(limit_order())
        mock.await_submit_confirmation(executor)
        executor.broker.adapter.set_transport_fault('cancel', _FAULT)

        executor.finish_remaining_orders(cancel_orders=True)

        assert order_steps(events) == [[
            'submitted', 'accepted', 'cancel_requested', 'unresolved', 'unaccounted']]
        assert events[-2].lost_request is OrderOperation.CANCEL
