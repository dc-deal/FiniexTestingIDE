"""
Protective Orders — a Cancel the Venue CARRIED OUT, Whose Answer Was Lost

The sibling of test_unresolved_protective_writes. There the cancel never reached the venue;
here the venue cancelled the stop and only its answer went missing. The next status read
finds the stop cancelled — and that read used to be booked as a broker REJECTION: a rejection
row, the cooldown, `on_order_rejected`, never `order_cancelled`, and the close parked behind
the cancel was never sent. Reproduced before the fix: five minutes later, stop-loss and
take-profit both breached, the position was open and no close had been submitted.

The same branch booked a cancel or expiry the venue made ON ITS OWN as a rejection too.

Also pinned here: a parked close counts as a close on its way, and a cancel stamps the
moment of its own write. No network: the protective mock, in both account models.
"""

from datetime import datetime, timedelta, timezone
from typing import List

import pytest

from python.framework.logging.global_logger import GlobalLogger
from python.framework.testing.mock_broker_adapter import MockExecutionMode
from python.framework.testing.mock_order_execution import MockOrderExecution
from python.framework.trading_env.broker_config import BrokerConfig
from python.framework.trading_env.live.live_trade_executor import LiveTradeExecutor
from python.framework.types.decision_event_types import DecisionEvent, OrderCancelledEvent
from python.framework.types.portfolio_types.portfolio_trade_record_types import CloseReason
from python.framework.types.trading_env_types.broker_types import BrokerType
from python.framework.types.trading_env_types.latency_simulator_types import PendingOperation
from python.framework.types.trading_env_types.order_types import (
    OpenOrderRequest,
    OrderAction,
    OrderDirection,
    OrderStatus,
    OrderType,
)
from tests.autotrader.protective_levels.conftest import VenueHoldsProtectionMock

_SYMBOL = 'BTCUSD'
_FAULT = 'venue unreachable'

ACCOUNT_MODELS = pytest.mark.parametrize('spot_mode', [False, True], ids=['margin', 'spot'])


def _protected_position(spot_mode: bool, lots: float = 0.10):
    """
    A live LONG whose stop the venue is holding.

    Args:
        spot_mode: Which account model the portfolio keeps
        lots: Position size

    Returns:
        (mock, executor, venue, position, protective order, decision events)
    """
    mock = MockOrderExecution(mode=MockExecutionMode.INSTANT_FILL)
    venue = VenueHoldsProtectionMock(mode=MockExecutionMode.INSTANT_FILL)
    executor = LiveTradeExecutor(
        broker_config=BrokerConfig(BrokerType.KRAKEN_SPOT, venue),
        initial_balance=100000.0,
        account_currency='USD',
        logger=GlobalLogger('LostCancelAnswer'),
        venue_held_protection=True,
        session_key='test',
        spot_mode=spot_mode,
        initial_balances={'USD': 100000.0, 'BTC': 0.0} if spot_mode else None,
    )
    events: List[DecisionEvent] = []
    executor.set_decision_event_sink(events.append)
    mock.feed_tick(executor, symbol=_SYMBOL, bid=50000.0, ask=50001.0)
    executor.open_order(OpenOrderRequest(
        symbol=_SYMBOL, order_type=OrderType.MARKET, direction=OrderDirection.LONG,
        lots=lots, stop_loss=49000.0))
    mock.feed_tick(executor, symbol=_SYMBOL, bid=50000.0, ask=50001.0)
    mock.await_submit_confirmation(executor)
    position = executor.get_open_positions()[0]
    protective = [p for p in executor._active_stop_orders if p.closes_position_id][0]
    assert position.protective_broker_ref, 'fixture: the venue confirmed the stop'
    return mock, executor, venue, position, protective, events


def _heartbeat(executor: LiveTradeExecutor) -> None:
    """One heartbeat as the loop runs it, after the worker answered what was queued."""
    executor.get_request_processor().flush_outbox()
    executor.set_current_time(datetime.now(timezone.utc))
    executor.heartbeat()


def _poll_now(executor: LiveTradeExecutor, protective) -> None:
    """Make the stop's next poll due at once, and run it to its answer."""
    protective.execution_state.last_polled_at_ms = 0.0
    for _ in range(3):
        _heartbeat(executor)


def _rejections(executor: LiveTradeExecutor) -> List:
    """Every rejection row in the order history."""
    return [r for r in executor.get_order_history() if r.status == OrderStatus.REJECTED]


def _executed_closes(executor: LiveTradeExecutor) -> List:
    """Every executed close row in the order history."""
    return [r for r in executor.get_order_history()
            if r.status == OrderStatus.EXECUTED and r.action == OrderAction.CLOSE]


def _close_behind_a_lost_cancel_answer(spot_mode: bool):
    """A close parked behind a cancel the venue carried out and never confirmed."""
    mock, executor, venue, position, protective, events = _protected_position(spot_mode)
    venue.lose_next_cancel_answer = True
    executor.close_position(position.position_id)
    mock.await_submit_confirmation(executor)
    assert protective.execution_state.in_flight_operation is PendingOperation.PENDING_CANCEL
    assert position.position_id in executor._deferred_closes, 'fixture: the close is parked'
    return mock, executor, venue, position, protective, events


class TestTheReadThatFindsTheStopCancelled:
    """The cancel happened; the read that learns it books it as a cancel."""

    @ACCOUNT_MODELS
    def test_no_rejection_is_booked(self, spot_mode):
        mock, executor, venue, position, protective, events = (
            _close_behind_a_lost_cancel_answer(spot_mode))

        _poll_now(executor, protective)

        assert not _rejections(executor), 'a cancel the venue carried out became a rejection'
        assert executor.get_execution_stats().orders_rejected == 0

    @ACCOUNT_MODELS
    def test_the_strategy_hears_order_cancelled(self, spot_mode):
        mock, executor, venue, position, protective, events = (
            _close_behind_a_lost_cancel_answer(spot_mode))

        _poll_now(executor, protective)

        assert any(isinstance(e, OrderCancelledEvent)
                   and e.order_id == protective.pending_order_id for e in events)

    @ACCOUNT_MODELS
    def test_the_parked_close_is_sent_exactly_once(self, spot_mode):
        mock, executor, venue, position, protective, events = (
            _close_behind_a_lost_cancel_answer(spot_mode))

        _poll_now(executor, protective)
        mock.feed_tick(executor, symbol=_SYMBOL, bid=50000.0, ask=50001.0)

        assert position.position_id not in executor._deferred_closes
        assert not executor.get_open_positions(), (
            'the close waited for a cancel the venue had already carried out')
        assert len(_executed_closes(executor)) == 1

    @ACCOUNT_MODELS
    def test_the_resolution_stops_asking(self, spot_mode):
        mock, executor, venue, position, protective, events = (
            _close_behind_a_lost_cancel_answer(spot_mode))

        _poll_now(executor, protective)

        assert protective.execution_state.resolution_deadline is None

    @ACCOUNT_MODELS
    def test_the_resolution_naming_it_cancelled_books_it_at_once(self, spot_mode):
        mock, executor, venue, position, protective, events = (
            _close_behind_a_lost_cancel_answer(spot_mode))
        _heartbeat(executor)
        protective.execution_state.resolution_next_at = (
            executor.get_current_time() - timedelta(seconds=1))
        # The ordinary poll is far from due; only the resolution's answer may trigger it
        protective.execution_state.last_polled_at_ms = float('inf')

        for _ in range(4):
            _heartbeat(executor)
        mock.feed_tick(executor, symbol=_SYMBOL, bid=50000.0, ask=50001.0)

        assert not executor.get_open_positions()
        assert not _rejections(executor)


class TestTheCancelNeverArrived:
    """The venue still holds the stop: the cancel is over, and so is the close it carried."""

    @ACCOUNT_MODELS
    def test_the_resolution_naming_it_resting_releases_the_operation(self, spot_mode):
        mock, executor, venue, position, protective, events = _protected_position(spot_mode)
        venue.set_transport_fault('cancel', _FAULT)
        executor.close_position(position.position_id)
        mock.await_submit_confirmation(executor)
        _heartbeat(executor)
        protective.execution_state.resolution_next_at = (
            executor.get_current_time() - timedelta(seconds=1))

        for _ in range(3):
            _heartbeat(executor)

        assert protective.execution_state.in_flight_operation is PendingOperation.NONE, (
            'left PENDING_CANCEL, every later cancel and amend is refused as busy')
        assert position.position_id not in executor._deferred_closes
        assert protective in executor._active_stop_orders, 'the venue still holds the stop'
        assert position.protective_broker_ref, 'and the local check stays aside for it'

    @ACCOUNT_MODELS
    def test_an_ordinary_poll_answering_resting_releases_nothing(self, spot_mode):
        # A query queued before the cancel may legitimately still say "open": only the
        # resolution's answer, asked after the write, may end the wait.
        mock, executor, venue, position, protective, events = _protected_position(spot_mode)
        venue.set_transport_fault('cancel', _FAULT)
        executor.close_position(position.position_id)
        mock.await_submit_confirmation(executor)

        _poll_now(executor, protective)

        assert protective.execution_state.in_flight_operation is PendingOperation.PENDING_CANCEL
        assert position.position_id in executor._deferred_closes


class TestTheVenueEndedTheStop:
    """A cancel or expiry the venue made on its own is a cancel, never a rejection."""

    @ACCOUNT_MODELS
    def test_it_is_booked_as_cancelled(self, spot_mode):
        mock, executor, venue, position, protective, events = _protected_position(spot_mode)
        venue.end_at_venue(protective.broker_ref, 'CANCELLED')

        _poll_now(executor, protective)

        assert not _rejections(executor)
        assert any(isinstance(e, OrderCancelledEvent)
                   and e.order_id == protective.pending_order_id for e in events)
        assert protective not in executor._active_stop_orders

    @ACCOUNT_MODELS
    def test_the_level_goes_back_to_the_local_check(self, spot_mode):
        mock, executor, venue, position, protective, events = _protected_position(spot_mode)
        venue.end_at_venue(protective.broker_ref, 'EXPIRED')

        _poll_now(executor, protective)

        assert not position.protective_broker_ref, (
            'the venue no longer holds the stop, so nobody would enforce the level')

    @ACCOUNT_MODELS
    def test_what_it_executed_before_it_ended_is_booked(self, spot_mode):
        mock, executor, venue, position, protective, events = _protected_position(spot_mode)
        venue.end_at_venue(
            protective.broker_ref, 'CANCELLED', filled_lots=0.04, fill_price=49000.0)

        _poll_now(executor, protective)

        assert executor.get_open_positions()[0].lots == pytest.approx(0.06), (
            'the venue closed 0.04 of the position and the book did not follow')


class TestAParkedCloseIsACloseOnItsWay:
    """While a close waits for its cancel, the position is being closed."""

    @ACCOUNT_MODELS
    def test_is_pending_close_answers_true(self, spot_mode):
        mock, executor, venue, position, protective, events = (
            _close_behind_a_lost_cancel_answer(spot_mode))

        assert executor.is_pending_close(position.position_id)

    @ACCOUNT_MODELS
    def test_a_breached_level_is_not_counted_on_every_tick(self, spot_mode):
        mock, executor, venue, position, protective, events = (
            _close_behind_a_lost_cancel_answer(spot_mode))
        before = executor.get_execution_stats().sl_tp_triggered

        for _ in range(5):
            executor._close_on_protective_level(position, 49000.0, CloseReason.SL_TRIGGERED)

        assert executor.get_execution_stats().sl_tp_triggered == before


class TestACancelStampsItsOwnWrite:
    """The settle window of a lost cancel answer runs from the cancel, not the submission."""

    @ACCOUNT_MODELS
    def test_last_write_at_is_the_cancel_moment(self, spot_mode):
        mock, executor, venue, position, protective, events = _protected_position(spot_mode)
        cancel_moment = datetime.now(timezone.utc) + timedelta(minutes=5)
        executor.set_current_time(cancel_moment)

        executor.close_position(position.position_id)

        assert protective.timing.last_write_at == cancel_moment
