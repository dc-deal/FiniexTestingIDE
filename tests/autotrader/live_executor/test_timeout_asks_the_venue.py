"""
The Live Order Path Books What the Venue Answers — Timeout, Heartbeat Poll, Failed Reads

Kraken answers a market order with a reference alone; the fill shows only to a later status
read. Three rules keep the book on the venue's side of that gap, and each is pinned here
against the real LiveTradeExecutor and a venue that fills first and answers only when asked:

- the heartbeat asks about MARKET orders and closes, so a quiet feed cannot hide a fill
- the fill timeout ASKS before it books, and hands an order nobody could answer about to
  the #487 resolution instead of discarding it
- a status read that FAILS is never the order's own rejection

Before these rules the timeout booked a filled close as `rejected · broker_error` and
discarded it: the venue had sold, the book still held the position, and the next close was
refused for insufficient funds. Every case runs in both account models.
"""

import time
from datetime import datetime, timedelta, timezone
from typing import List

import pytest

from python.framework.exceptions.connection_errors import ConnectionAttemptFailedError
from python.framework.logging.global_logger import GlobalLogger
from python.framework.trading_env.broker_config import BrokerConfig
from python.framework.trading_env.live.live_request_processor import LiveRequestProcessor
from python.framework.trading_env.live.live_trade_executor import LiveTradeExecutor
from python.framework.types.decision_event_types import DecisionEvent, OrderCancelledEvent
from python.framework.types.live_types.live_execution_types import (
    BrokerOrderStatus,
    TimeoutConfig,
)
from python.framework.types.live_types.live_request_types import OrderResolveResponse
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.trading_env_types.broker_types import BrokerType
from python.framework.types.trading_env_types.order_types import (
    OpenOrderRequest,
    OrderDirection,
    OrderEndReason,
    OrderInitiator,
    OrderStatus,
    OrderType,
)
from tests.autotrader.live_executor.conftest import AcknowledgingVenueMock, LevelRecorder

_SYMBOL = 'BTCUSD'
_LOTS = 0.01
_BID, _ASK = 50000.0, 50001.0

ACCOUNT_MODELS = pytest.mark.parametrize('spot_mode', [False, True], ids=['margin', 'spot'])


def _executor(venue: AcknowledgingVenueMock, spot_mode: bool) -> LiveTradeExecutor:
    """
    A live executor on the acknowledging venue.

    Args:
        venue: The venue mock
        spot_mode: Which account model the portfolio keeps

    Returns:
        The executor under test
    """
    return LiveTradeExecutor(
        broker_config=BrokerConfig(BrokerType.KRAKEN_SPOT, venue),
        initial_balance=100000.0,
        account_currency='USD',
        logger=GlobalLogger(name='TimeoutAsksTheVenue'),
        timeout_config=TimeoutConfig(order_timeout_seconds=30.0),
        spot_mode=spot_mode,
        initial_balances={'USD': 100000.0, 'BTC': 0.0} if spot_mode else None,
        session_key='tatv',
    )


def _tick(executor: LiveTradeExecutor) -> None:
    """Deliver one tick, after the worker has answered everything queued."""
    executor.get_request_processor().flush_outbox()
    executor.on_tick(TickData(
        timestamp=datetime.now(timezone.utc), symbol=_SYMBOL, bid=_BID, ask=_ASK))


def _heartbeat(executor: LiveTradeExecutor) -> None:
    """One heartbeat as the loop runs it: answers absorbed first, the clock set, then due work."""
    executor.get_request_processor().flush_outbox()
    executor.set_current_time(datetime.now(timezone.utc))
    executor.heartbeat()


def _acknowledged_open(executor: LiveTradeExecutor) -> str:
    """
    Open a market LONG and absorb the venue's acknowledgement — reference, no fill.

    Args:
        executor: The executor under test

    Returns:
        The order id
    """
    _tick(executor)
    result = executor.open_order(OpenOrderRequest(
        symbol=_SYMBOL, order_type=OrderType.MARKET,
        direction=OrderDirection.LONG, lots=_LOTS))
    processor = executor.get_request_processor()
    processor.flush_outbox()
    processor.drain_inbox()
    assert processor.get_order(result.order_id).broker_ref, 'fixture: no acknowledgement'
    return result.order_id


def _expire_fill_timeout(executor: LiveTradeExecutor, order_id: str) -> None:
    """Move an order's fill timeout into the past."""
    pending = executor.get_request_processor().get_order(order_id)
    pending.timing.order_timeout_deadline_monotonic = time.monotonic() - 1.0


def _rejections(executor: LiveTradeExecutor) -> List:
    """Every rejection row in the order history."""
    return [r for r in executor.get_order_history() if r.status == OrderStatus.REJECTED]


def _rows(executor: LiveTradeExecutor, status: OrderStatus) -> List:
    """Every row of one status in the order history."""
    return [r for r in executor.get_order_history() if r.status is status]


class TestTheHeartbeatSeesTheFill:
    """A fill in a quiet feed is read on the heartbeat, not lost to the timeout."""

    @ACCOUNT_MODELS
    def test_a_market_open_filled_without_a_further_tick_is_booked(self, spot_mode):
        venue = AcknowledgingVenueMock()
        executor = _executor(venue, spot_mode)
        _acknowledged_open(executor)

        _heartbeat(executor)
        _heartbeat(executor)

        positions = executor.get_open_positions()
        assert len(positions) == 1 and positions[0].lots == pytest.approx(_LOTS), (
            'the venue filled the order and no tick followed — the heartbeat never asked')
        assert not _rejections(executor)

    @ACCOUNT_MODELS
    def test_a_close_filled_without_a_further_tick_is_booked(self, spot_mode):
        venue = AcknowledgingVenueMock()
        executor = _executor(venue, spot_mode)
        _acknowledged_open(executor)
        _tick(executor)
        position = executor.get_open_positions()[0]

        executor.close_position(position.position_id)
        executor.get_request_processor().flush_outbox()
        executor.get_request_processor().drain_inbox()
        _heartbeat(executor)
        _heartbeat(executor)

        assert not executor.get_open_positions(), (
            'the venue sold and the book still holds the position')
        assert not _rejections(executor)

    @ACCOUNT_MODELS
    def test_the_tick_does_not_ask_while_the_heartbeat_question_is_in_flight(
            self, spot_mode):
        # Asked twice, the second answer is booked against an order the first one removed.
        venue = AcknowledgingVenueMock()
        executor = _executor(venue, spot_mode)
        order_id = _acknowledged_open(executor)
        pending = executor.get_request_processor().get_order(order_id)
        pending.execution_state.in_flight_query = True

        _tick(executor)

        assert venue.queries.count(pending.broker_ref) == 0

    @ACCOUNT_MODELS
    def test_a_fill_learned_by_the_resolution_is_booked_without_a_tick(self, spot_mode):
        # The submit answer is lost after the venue filled the order. The resolution finds it
        # by our own key and names it filled — and only the poll books, so the poll must not
        # wait for a tick that may not come.
        venue = AcknowledgingVenueMock()
        venue.lose_next_submit_answer = True
        executor = _executor(venue, spot_mode)
        _tick(executor)
        result = executor.open_order(OpenOrderRequest(
            symbol=_SYMBOL, order_type=OrderType.MARKET,
            direction=OrderDirection.LONG, lots=_LOTS))
        _heartbeat(executor)
        pending = executor.get_request_processor().get_order(result.order_id)
        assert pending is not None and pending.broker_ref is None, 'fixture: answer lost'
        pending.execution_state.resolution_next_at = (
            executor.get_current_time() - timedelta(seconds=1))

        for _ in range(5):
            _heartbeat(executor)

        assert len(executor.get_open_positions()) == 1, (
            'the venue filled the order, the resolution learned it, and nothing booked it')
        assert not _rejections(executor)


class TestTheTimeoutAsksBeforeItBooks:
    """The timeout reads the order's status and books what the venue says."""

    @ACCOUNT_MODELS
    def test_an_order_filled_by_its_timeout_is_booked_as_executed(self, spot_mode):
        venue = AcknowledgingVenueMock()
        executor = _executor(venue, spot_mode)
        order_id = _acknowledged_open(executor)
        _expire_fill_timeout(executor, order_id)

        _heartbeat(executor)

        assert len(executor.get_open_positions()) == 1
        assert not _rejections(executor), 'a filled order was booked as timed out'
        assert executor.get_execution_stats().orders_rejected == 0

    @ACCOUNT_MODELS
    def test_reads_failing_through_the_timeout_hand_the_order_to_the_resolution(
            self, spot_mode):
        venue = AcknowledgingVenueMock()
        executor = _executor(venue, spot_mode)
        order_id = _acknowledged_open(executor)
        venue.query_fault = ConnectionAttemptFailedError('HTTP 502', terminal=False)
        _expire_fill_timeout(executor, order_id)

        _heartbeat(executor)

        pending = executor.get_request_processor().get_order(order_id)
        assert pending is not None, (
            'the order was discarded although nobody could say whether it filled')
        assert pending.execution_state.resolution_deadline is not None
        assert pending.execution_state.timeout_handed_to_resolution
        assert not _rejections(executor)

    @ACCOUNT_MODELS
    def test_the_resolution_then_books_the_fill(self, spot_mode):
        venue = AcknowledgingVenueMock()
        executor = _executor(venue, spot_mode)
        order_id = _acknowledged_open(executor)
        venue.query_fault = ConnectionAttemptFailedError('HTTP 502', terminal=False)
        _expire_fill_timeout(executor, order_id)
        _heartbeat(executor)
        venue.query_fault = None
        pending = executor.get_request_processor().get_order(order_id)
        pending.execution_state.resolution_next_at = (
            executor.get_current_time() - timedelta(seconds=1))

        for _ in range(4):
            _heartbeat(executor)

        assert len(executor.get_open_positions()) == 1, (
            'the resolution learned the fill and nothing booked it')
        assert not _rejections(executor)

    @ACCOUNT_MODELS
    def test_a_second_unanswerable_timeout_gives_the_order_up_as_unaccounted(
            self, spot_mode):
        venue = AcknowledgingVenueMock()
        executor = _executor(venue, spot_mode)
        order_id = _acknowledged_open(executor)
        venue.query_fault = ConnectionAttemptFailedError('HTTP 502', terminal=False)
        _expire_fill_timeout(executor, order_id)
        _heartbeat(executor)
        pending = executor.get_request_processor().get_order(order_id)
        # The resolution names the order as still working, which re-arms the timeout
        executor._resolve_order_is_named(pending, OrderResolveResponse(
            order_id=order_id, status=BrokerOrderStatus.PENDING,
            broker_ref=pending.broker_ref))
        _expire_fill_timeout(executor, order_id)

        _heartbeat(executor)

        assert executor.get_request_processor().get_order(order_id) is None
        assert not _rejections(executor), (
            'an order the venue acknowledged and nobody could ask about was blamed on '
            'the venue')
        unaccounted = _rows(executor, OrderStatus.UNACCOUNTED)
        assert len(unaccounted) == 1
        assert unaccounted[0].initiator is OrderInitiator.FRAMEWORK
        assert unaccounted[0].end_reason is OrderEndReason.ORDER_TIMEOUT

    @ACCOUNT_MODELS
    def test_a_working_order_is_cancelled_and_booked_as_a_cancel(self, spot_mode):
        venue = AcknowledgingVenueMock()
        venue.fill_market_orders = False
        executor = _executor(venue, spot_mode)
        order_id = _acknowledged_open(executor)
        ref = executor.get_request_processor().get_order(order_id).broker_ref
        _expire_fill_timeout(executor, order_id)

        _heartbeat(executor)

        assert executor.get_request_processor().get_order(order_id) is None
        assert venue.cancels == [ref], 'the confirmed cancel was sent twice'
        assert not _rejections(executor), 'the venue refused nothing — we cancelled it'
        cancelled = _rows(executor, OrderStatus.CANCELLED)
        assert len(cancelled) == 1
        assert cancelled[0].initiator is OrderInitiator.FRAMEWORK
        assert cancelled[0].end_reason is OrderEndReason.ORDER_TIMEOUT

    @ACCOUNT_MODELS
    def test_a_cancel_refused_because_the_order_filled_books_the_fill(self, spot_mode):
        class FillsWhileTheCancelTravels(AcknowledgingVenueMock):
            def do_request_cancel(self, payload):
                self.set_venue_status(
                    payload['broker_ref'], 'FILLED', filled_lots=_LOTS, fill_price=_ASK)
                return super().do_request_cancel(payload)

        venue = FillsWhileTheCancelTravels()
        venue.fill_market_orders = False
        executor = _executor(venue, spot_mode)
        order_id = _acknowledged_open(executor)
        _expire_fill_timeout(executor, order_id)

        _heartbeat(executor)

        assert len(executor.get_open_positions()) == 1, (
            '"Unknown order" on the cancel meant the order was gone — as a fill')
        assert not _rejections(executor)


    @ACCOUNT_MODELS
    def test_a_part_executed_while_the_cancel_travelled_is_booked(self, spot_mode):
        """
        The read before the cancel is older than the cancel.

        0.004 of 0.01 executes between our read and our cancel; the venue then cancels the
        rest and confirms. Decided from the first read, that was a clean cancel and the
        0.004 the venue bought was missing from the book.
        """
        class ExecutesWhileTheCancelTravels(AcknowledgingVenueMock):
            def do_request_cancel(self, payload):
                self.set_venue_status(
                    payload['broker_ref'], 'PENDING', filled_lots=0.004, fill_price=_ASK)
                return super().do_request_cancel(payload)

        venue = ExecutesWhileTheCancelTravels()
        venue.fill_market_orders = False
        executor = _executor(venue, spot_mode)
        order_id = _acknowledged_open(executor)
        _expire_fill_timeout(executor, order_id)

        _heartbeat(executor)

        positions = executor.get_open_positions()
        assert len(positions) == 1, 'the executed part is a position the venue opened'
        assert positions[0].lots == pytest.approx(0.004)
        assert not _rows(executor, OrderStatus.CANCELLED), (
            'one row per order: the fill of what executed, not a clean cancel')
        assert executor.get_request_processor().get_order(order_id) is None


class TestAFailedReadIsNotARejection:
    """A status read that fails refused the QUESTION, not the order."""

    @ACCOUNT_MODELS
    def test_a_terminal_read_failure_keeps_the_market_order(self, spot_mode):
        venue = AcknowledgingVenueMock()
        executor = _executor(venue, spot_mode)
        order_id = _acknowledged_open(executor)
        venue.query_fault = ConnectionError('Kraken API error: [EAPI:Invalid nonce]')

        _tick(executor)

        assert executor.get_request_processor().get_order(order_id) is not None, (
            'a refused status QUERY dropped an order the venue had filled')
        assert not _rejections(executor)

    @ACCOUNT_MODELS
    def test_a_terminal_read_failure_keeps_a_resting_order(self, spot_mode):
        venue = AcknowledgingVenueMock()
        executor = _executor(venue, spot_mode)
        _tick(executor)
        result = executor.open_order(OpenOrderRequest(
            symbol=_SYMBOL, order_type=OrderType.LIMIT,
            direction=OrderDirection.LONG, lots=_LOTS, price=49000.0))
        processor = executor.get_request_processor()
        processor.flush_outbox()
        processor.drain_inbox()
        venue.query_fault = ConnectionError('Kraken API error: [EAPI:Invalid nonce]')

        _tick(executor)
        _heartbeat(executor)

        assert any(p.pending_order_id == result.order_id
                   for p in executor._active_limit_orders), (
            'a refused status QUERY dropped an order resting at the venue')
        assert not _rejections(executor)

    def test_it_is_reported_once_per_order_as_an_error(self):
        # Not a transport blip, so it reaches the error pot — but once: the poll asks again
        # every cycle, and the same line every five seconds would bury the channel.
        recorder = LevelRecorder()
        processor = LiveRequestProcessor(logger=recorder, timeout_config=TimeoutConfig())
        error = ConnectionError('Kraken API error: [EAPI:Invalid nonce]')
        for _ in range(3):
            answer = processor._failure_response(
                error, broker_ref='TX-1', timestamp=datetime.now(timezone.utc),
                operation='status query', self_healing=True, is_read=True)
            assert answer.status == BrokerOrderStatus.UNRESOLVED

        assert recorder.levels == ['error']


class TestTheTimeoutRunsOnTheMonotonicClock:
    """A duration never reads the canonical clock, which a mock session splits in two."""

    @ACCOUNT_MODELS
    def test_a_rearmed_timeout_survives_a_replay_clock_months_in_the_past(self, spot_mode):
        venue = AcknowledgingVenueMock()
        executor = _executor(venue, spot_mode)
        order_id = _acknowledged_open(executor)
        pending = executor.get_request_processor().get_order(order_id)
        # A mock session's tick sets the canonical clock to the replayed tick's own time
        executor.set_current_time(datetime(2026, 1, 25, 20, 10, tzinfo=timezone.utc))

        executor._resolve_order_is_named(pending, OrderResolveResponse(
            order_id=order_id, status=BrokerOrderStatus.PENDING,
            broker_ref=pending.broker_ref))

        assert executor.get_request_processor().check_timeouts() == [], (
            'the re-armed deadline was set from replay time and expired at once')


class TestAnOrderTheVenueEnded:
    """A market order the venue cancels or expires is never booked as a rejection."""

    @ACCOUNT_MODELS
    def test_nothing_executed_drops_it_and_says_cancelled(self, spot_mode):
        venue = AcknowledgingVenueMock()
        venue.fill_market_orders = False
        executor = _executor(venue, spot_mode)
        events: List[DecisionEvent] = []
        executor.set_decision_event_sink(events.append)
        order_id = _acknowledged_open(executor)
        ref = executor.get_request_processor().get_order(order_id).broker_ref
        venue.set_venue_status(ref, 'CANCELLED')

        _tick(executor)

        assert executor.get_request_processor().get_order(order_id) is None
        assert not _rejections(executor)
        assert executor.get_execution_stats().orders_rejected == 0
        assert any(isinstance(e, OrderCancelledEvent) and e.order_id == order_id
                   for e in events)

    @ACCOUNT_MODELS
    def test_what_executed_before_it_ended_is_booked(self, spot_mode):
        venue = AcknowledgingVenueMock()
        venue.fill_market_orders = False
        executor = _executor(venue, spot_mode)
        order_id = _acknowledged_open(executor)
        ref = executor.get_request_processor().get_order(order_id).broker_ref
        venue.set_venue_status(ref, 'EXPIRED', filled_lots=0.004, fill_price=_ASK)

        _tick(executor)

        positions = executor.get_open_positions()
        assert len(positions) == 1 and positions[0].lots == pytest.approx(0.004), (
            'the executed part moved money and was not booked')
        assert not _rejections(executor)
