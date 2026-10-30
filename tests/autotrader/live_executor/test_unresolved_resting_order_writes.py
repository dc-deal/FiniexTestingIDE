"""
Live Executor — a Write on a Resting Order Whose Answer Was Lost (#487)

A cancel or an amend of an ordinary resting order can lose its answer like a submit. It then books
nothing: the order stays, its operation stays in flight, and the resolution decides. Booking it
anyway drops an order the venue may still hold, or shows a price the venue never took.

These pin what happens to the order itself. Asking, and what the answer may become, is
test_unresolved_write_resolution.py. No network: the mock adapter with injected transport faults.
"""

from datetime import datetime, timedelta, timezone
from typing import List

import pytest

from python.framework.exceptions.connection_errors import ConnectionAttemptFailedError
from python.framework.testing.mock_broker_adapter import MockExecutionMode
from python.framework.testing.mock_order_execution import MockOrderExecution
from python.framework.trading_env.live.live_trade_executor import LiveTradeExecutor
from python.framework.types.decision_event_types import DecisionEvent, OrderCancelledEvent
from python.framework.types.live_types.live_execution_types import (
    BrokerOrderStatus,
    BrokerResponse,
)
from python.framework.types.trading_env_types.latency_simulator_types import (
    PendingOperation,
    PendingOrder,
)
from python.framework.types.trading_env_types.order_types import (
    OpenOrderRequest,
    OrderDirection,
    OrderStatus,
    OrderType,
)

_SYMBOL = 'BTCUSD'
_FAULT = 'venue unreachable'

ACCOUNT_MODELS = pytest.mark.parametrize('spot_mode', [False, True], ids=['margin', 'spot'])


def _resting_limit(spot_mode: bool) -> tuple:
    """
    A LIMIT the venue has confirmed and keeps resting until the test ends it.

    Args:
        spot_mode: Which account model the portfolio keeps

    Returns:
        (mock, executor, the resting order)
    """
    mock = MockOrderExecution(
        mode=MockExecutionMode.TIMEOUT, spot_mode=spot_mode,
        initial_balances={'USD': 100000.0, 'BTC': 0.0} if spot_mode else None)
    executor = mock.create_executor()
    mock.feed_tick(executor, symbol=_SYMBOL, bid=50000.0, ask=50001.0)
    executor.open_order(OpenOrderRequest(
        symbol=_SYMBOL, order_type=OrderType.LIMIT, direction=OrderDirection.LONG,
        lots=0.001, price=40000.0))
    mock.await_submit_confirmation(executor)
    pending = executor._active_limit_orders[0]
    assert pending.broker_ref, 'fixture: the venue confirmed it'
    return mock, executor, pending


def _heartbeat(executor: LiveTradeExecutor) -> None:
    """One heartbeat as the loop runs it, after the worker answered what was queued."""
    executor.get_request_processor().flush_outbox()
    executor.set_current_time(datetime.now(timezone.utc))
    executor.heartbeat()


def _poll_now(executor: LiveTradeExecutor, pending: PendingOrder) -> None:
    """Make the order's next poll due at once, and run it to its answer."""
    pending.execution_state.last_polled_at_ms = 0.0
    for _ in range(3):
        _heartbeat(executor)


def _exhaust_resolution(executor: LiveTradeExecutor, pending: PendingOrder) -> None:
    """Run the resolution past its ceiling without waiting for the wall clock."""
    executor.heartbeat()
    state = pending.execution_state
    state.resolution_deadline = executor.get_current_time() - timedelta(seconds=1)
    state.resolution_next_at = None
    state.resolution_in_flight = False
    executor._process_unresolved_orders()


def _cancelled_rows(executor: LiveTradeExecutor, pending: PendingOrder) -> List:
    """Every cancelled row the order has in the order history."""
    return [row for row in executor.get_order_history()
            if row.order_id == pending.pending_order_id and row.status is OrderStatus.CANCELLED]


class TestAnUnresolvedAmendWritesNoProvisionalValue:
    """The local shadow must not show a price the venue may never have taken."""

    @ACCOUNT_MODELS
    def test_the_price_is_not_moved(self, spot_mode):
        mock, executor, pending = _resting_limit(spot_mode)
        before = pending.entry_price
        executor.broker.adapter.set_transport_fault('modify', _FAULT)

        executor.modify_limit_order(pending.pending_order_id, new_price=41000.0)
        mock.await_submit_confirmation(executor)

        assert pending.entry_price == before, (
            f'the shadow moved to {pending.entry_price} on an answer that never came')

    @ACCOUNT_MODELS
    def test_what_was_asked_stays_parked(self, spot_mode):
        mock, executor, pending = _resting_limit(spot_mode)
        executor.broker.adapter.set_transport_fault('modify', _FAULT)

        executor.modify_limit_order(pending.pending_order_id, new_price=41000.0)
        mock.await_submit_confirmation(executor)

        assert pending.execution_state.pending_modification is not None, (
            'discarding it would lose what was asked for, and applying it would claim it happened')
        assert pending.execution_state.in_flight_operation is PendingOperation.PENDING_MODIFY, (
            'the one-outstanding-amend guard stays closed until the venue answers')


class TestACancelAnswerNamingNothingBooksNothing:
    """
    The venue ANSWERED and named nothing it cancelled — as open a question as a lost answer.

    Kraken does not answer this way, it raises for an order that is gone (measured 2026-09-13), so
    the second adapter (#209) is what reaches it.
    """

    @staticmethod
    def _cancel_answered_unknown(spot_mode: bool) -> tuple:
        """A cancel the venue answered by naming nothing."""
        mock, executor, pending = _resting_limit(spot_mode)
        executor.broker.adapter.parse_cancel_response = (
            lambda raw, broker_ref, timestamp: BrokerResponse(
                broker_ref=broker_ref, status=BrokerOrderStatus.UNKNOWN, timestamp=timestamp))
        executor.cancel_limit_order(pending.pending_order_id)
        mock.await_submit_confirmation(executor)
        return mock, executor, pending

    @ACCOUNT_MODELS
    def test_the_order_stays(self, spot_mode):
        _, executor, pending = self._cancel_answered_unknown(spot_mode)

        assert pending in executor._active_limit_orders, 'the venue may still be holding it'
        assert not _cancelled_rows(executor, pending)

    @ACCOUNT_MODELS
    def test_the_resolution_asks_what_became_of_it(self, spot_mode):
        _, executor, pending = self._cancel_answered_unknown(spot_mode)

        executor.heartbeat()

        assert pending.execution_state.in_flight_operation is PendingOperation.PENDING_CANCEL
        assert pending.execution_state.resolution_deadline is not None


class TestTheCeilingEndsTheWait:
    """Booking nothing cannot mean waiting forever: that is its own defect."""

    @staticmethod
    def _cancel_nobody_answered(spot_mode: bool) -> tuple:
        """A cancel that never reached the venue, past the end of the resolution."""
        mock, executor, pending = _resting_limit(spot_mode)
        executor.broker.adapter.set_transport_fault('cancel', _FAULT)
        executor.cancel_limit_order(pending.pending_order_id)
        mock.await_submit_confirmation(executor)
        _exhaust_resolution(executor, pending)
        return mock, executor, pending

    @ACCOUNT_MODELS
    def test_the_order_is_operable_again(self, spot_mode):
        """
        The watchdog nothing else supplies: `check_timeouts` iterates the processor's own store,
        and a resting order is not in it, so an order left PENDING_CANCEL would sit there until
        the session ends while every further operation on it is refused as busy.
        """
        _, _, pending = self._cancel_nobody_answered(spot_mode)

        assert pending.execution_state.in_flight_operation is PendingOperation.NONE

    @ACCOUNT_MODELS
    def test_the_order_itself_is_not_dropped(self, spot_mode):
        _, executor, pending = self._cancel_nobody_answered(spot_mode)

        assert pending in executor._active_limit_orders


class TestTheReadThatFindsTheOrderCancelled:
    """The venue carried the cancel out and its answer never came back: a poll learns of it."""

    @staticmethod
    def _cancel_with_a_lost_answer(spot_mode: bool) -> tuple:
        """A cancel the venue carried out, whose answer was lost, with the decision events."""
        mock, executor, pending = _resting_limit(spot_mode)
        events: List[DecisionEvent] = []
        executor.set_decision_event_sink(events.append)
        adapter = executor.broker.adapter
        carry_out = adapter.do_request_cancel

        def lose_the_answer(payload):
            carry_out(payload)
            raise ConnectionAttemptFailedError('read timeout after CancelOrder', terminal=False)

        adapter.do_request_cancel = lose_the_answer
        executor.cancel_limit_order(pending.pending_order_id)
        mock.await_submit_confirmation(executor)
        assert pending.execution_state.in_flight_operation is PendingOperation.PENDING_CANCEL
        return executor, pending, events

    @ACCOUNT_MODELS
    def test_it_is_booked_as_cancelled_and_not_as_a_rejection(self, spot_mode):
        executor, pending, _ = self._cancel_with_a_lost_answer(spot_mode)

        _poll_now(executor, pending)

        assert pending not in executor._active_limit_orders
        assert len(_cancelled_rows(executor, pending)) == 1
        assert not [row for row in executor.get_order_history()
                    if row.status is OrderStatus.REJECTED]

    @ACCOUNT_MODELS
    def test_the_strategy_hears_order_cancelled(self, spot_mode):
        executor, pending, events = self._cancel_with_a_lost_answer(spot_mode)

        _poll_now(executor, pending)

        assert [event.order_id for event in events if isinstance(event, OrderCancelledEvent)] == [
            pending.pending_order_id]

    @ACCOUNT_MODELS
    def test_the_resolution_stops_asking(self, spot_mode):
        """Left armed, it would reach its ceiling and block new entries over a settled order."""
        executor, pending, _ = self._cancel_with_a_lost_answer(spot_mode)

        _poll_now(executor, pending)

        assert pending.execution_state.resolution_deadline is None


    @ACCOUNT_MODELS
    def test_the_entry_block_lifts_when_a_poll_finds_it_cancelled_after_the_ceiling(self, spot_mode):
        """The poll is the one route that lifts it here: the resolution has already given up."""
        executor, pending, _ = self._cancel_with_a_lost_answer(spot_mode)
        _exhaust_resolution(executor, pending)
        assert executor.get_unresolved_at_ceiling(), 'fixture: the ceiling was reached'

        _poll_now(executor, pending)

        assert not executor.get_unresolved_at_ceiling(), 'new entries stay blocked over a settled order'


class TestTheCancelNeverArrived:
    """The venue still holds the order: the cancel is over, and the order is not."""

    @ACCOUNT_MODELS
    def test_the_resolution_naming_it_resting_releases_the_operation(self, spot_mode):
        mock, executor, pending = _resting_limit(spot_mode)
        executor.broker.adapter.set_transport_fault('cancel', _FAULT)
        executor.cancel_limit_order(pending.pending_order_id)
        mock.await_submit_confirmation(executor)
        _heartbeat(executor)
        pending.execution_state.resolution_next_at = (
            executor.get_current_time() - timedelta(seconds=1))

        for _ in range(3):
            _heartbeat(executor)

        assert pending.execution_state.in_flight_operation is PendingOperation.NONE, (
            'left PENDING_CANCEL, every later cancel and amend is refused as busy')
        assert pending in executor._active_limit_orders, 'the venue still holds it'

    @ACCOUNT_MODELS
    def test_an_ordinary_poll_answering_resting_releases_nothing(self, spot_mode):
        """A query queued before the cancel may still say open: only the resolution may end the wait."""
        mock, executor, pending = _resting_limit(spot_mode)
        executor.broker.adapter.set_transport_fault('cancel', _FAULT)
        executor.cancel_limit_order(pending.pending_order_id)
        mock.await_submit_confirmation(executor)

        _poll_now(executor, pending)

        assert pending.execution_state.in_flight_operation is PendingOperation.PENDING_CANCEL


class TestACancelStampsItsOwnWrite:
    """The settle window of a lost cancel answer runs from the cancel, not the submission."""

    @ACCOUNT_MODELS
    def test_last_write_at_is_the_cancel_moment(self, spot_mode):
        _, executor, pending = _resting_limit(spot_mode)
        cancel_moment = datetime.now(timezone.utc) + timedelta(minutes=5)
        executor.set_current_time(cancel_moment)

        executor.cancel_limit_order(pending.pending_order_id)

        assert pending.timing.last_write_at == cancel_moment
