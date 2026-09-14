"""
Protective Orders — a Write Whose Answer Was Lost Books NOTHING (#487)

This is the expensive half of #487, and the reason it is a protective-order suite rather
than a transport one: the write that hurts when it is guessed at is the CANCEL of a stop
the venue is holding.

The success path of `_handle_cancel_response` drops the order from the resting list, clears
the protective stamp, emits `order_cancelled` and RELEASES the deferred close. Run for an
UNRESOLVED answer — which is what "not rejected" used to mean — it sends a market close
beside a stop that may still be resting at the venue. That is exactly the double-fill
#503's cancel-before-close ordering exists to prevent, reached through a transport fault
instead of a race.

So an unresolved write books nothing at all, and the deferred close keeps WAITING. What
ends the wait is the resolution: either the venue finally names the order, or the ceiling
abandons the close with an error the operator sees. The one thing that must never happen is
the close going out while the stop's fate is unknown.

No network: the protective mock with injected transport faults.
"""

from datetime import timedelta

from python.framework.logging.global_logger import GlobalLogger
from python.framework.testing.mock_broker_adapter import MockExecutionMode
from python.framework.testing.mock_order_execution import MockOrderExecution
from python.framework.trading_env.broker_config import BrokerConfig
from python.framework.trading_env.live.live_trade_executor import LiveTradeExecutor
from python.framework.types.trading_env_types.broker_types import BrokerType
from python.framework.types.trading_env_types.latency_simulator_types import (
    PendingOperation,
    PendingOrderAction,
)
from python.framework.types.trading_env_types.order_types import (
    OpenOrderRequest,
    OrderDirection,
    OrderType,
)

from tests.autotrader.protective_levels.conftest import VenueHoldsProtectionMock

_SYMBOL = 'BTCUSD'
_FAULT = 'venue unreachable'


def _protected_position(lots: float = 0.10, stop_loss: float = 49000.0):
    """
    A live LONG whose stop the venue is already holding.

    Args:
        lots: Position size
        stop_loss: The declared level

    Returns:
        (mock, executor, position, protective order)
    """
    mock = MockOrderExecution(mode=MockExecutionMode.INSTANT_FILL)
    executor = LiveTradeExecutor(
        broker_config=BrokerConfig(
            BrokerType.KRAKEN_SPOT,
            VenueHoldsProtectionMock(mode=MockExecutionMode.INSTANT_FILL)),
        initial_balance=100000.0,
        account_currency='USD',
        logger=GlobalLogger('UnresolvedProtectiveWrites'),
        venue_held_protection=True,
        session_key='test',
    )
    mock.feed_tick(executor, symbol=_SYMBOL, bid=50000.0, ask=50001.0)
    executor.open_order(OpenOrderRequest(
        symbol=_SYMBOL, order_type=OrderType.MARKET, direction=OrderDirection.LONG,
        lots=lots, stop_loss=stop_loss))
    mock.feed_tick(executor, symbol=_SYMBOL, bid=50000.0, ask=50001.0)
    mock.await_submit_confirmation(executor)
    position = executor.get_open_positions()[0]
    protective = [p for p in executor._active_stop_orders if p.closes_position_id][0]
    assert position.protective_broker_ref, 'the venue confirmed it'
    return mock, executor, position, protective


def _close_behind_an_unanswered_cancel():
    """A close waiting behind a cancel the venue never answered."""
    mock, executor, position, protective = _protected_position()
    executor.broker.adapter.set_transport_fault('cancel', _FAULT)

    executor.close_position(position.position_id)
    mock.await_submit_confirmation(executor)
    return mock, executor, position, protective


class TestAnUnresolvedCancelBooksNothing:
    """The order is not dropped, the stamp is not cleared, the close is not released."""

    def test_the_order_stays_in_its_resting_list(self):
        mock, executor, position, protective = _close_behind_an_unanswered_cancel()

        assert protective in executor._active_stop_orders, (
            'the venue may still be holding this stop — dropping it is how a resting order '
            'becomes an orphan')

    def test_the_protective_stamp_stays(self):
        mock, executor, position, protective = _close_behind_an_unanswered_cancel()

        assert position.protective_broker_ref, (
            'clearing the stamp hands the level back to the local check while the venue may '
            'still be enforcing it — both would fire')

    def test_the_deferred_close_is_not_released(self):
        """
        The one that costs money.

        Releasing it sends a market close beside a stop that may still be resting. The
        close KEEPS WAITING instead — the resolution decides, not a guess.
        """
        mock, executor, position, protective = _close_behind_an_unanswered_cancel()

        assert position.position_id in executor._deferred_closes, (
            'the close was released or abandoned on an answer the venue never gave')
        assert not executor.get_request_processor().get_pending_orders(
            PendingOrderAction.CLOSE), (
            'a market close reached the venue while the stop\'s fate was unknown')
        assert executor.get_open_positions(), 'the position is still open, as it must be'

    def test_the_operation_stays_in_flight_so_nothing_races_it(self):
        mock, executor, position, protective = _close_behind_an_unanswered_cancel()

        assert protective.execution_state.in_flight_operation is PendingOperation.PENDING_CANCEL

    def test_and_the_resolution_is_asking(self):
        mock, executor, position, protective = _close_behind_an_unanswered_cancel()
        executor.heartbeat()

        assert protective.execution_state.resolution_deadline is not None, (
            'nothing is asking, so nothing will ever end this wait')


class TestTheCeilingEndsTheWait:
    """
    Booking nothing cannot mean waiting forever — that is its own defect.

    Without a release the deferred close would sit in `_deferred_closes` for the rest of
    the session, and `close_position` is a no-op while it does: the position could never be
    closed again, while the framework's own stop check re-requests the close every tick.
    """

    def test_the_close_is_abandoned_rather_than_left_hanging(self):
        mock, executor, position, protective = _close_behind_an_unanswered_cancel()
        _exhaust_resolution(executor, protective)

        assert position.position_id not in executor._deferred_closes, (
            'the close is still waiting behind a cancel nobody will ever answer — the '
            'position can never be closed again this session')

    def test_the_order_is_operable_again(self):
        """
        The watchdog nothing else supplies: `check_timeouts` iterates the processor's own
        store, and a resting order is not in it, so a pending left PENDING_CANCEL would sit
        there until session end while every further operation on it is refused as busy.
        """
        mock, executor, position, protective = _close_behind_an_unanswered_cancel()
        _exhaust_resolution(executor, protective)

        assert protective.execution_state.in_flight_operation is PendingOperation.NONE

    def test_the_order_itself_is_still_not_dropped(self):
        mock, executor, position, protective = _close_behind_an_unanswered_cancel()
        _exhaust_resolution(executor, protective)

        assert protective in executor._active_stop_orders, (
            'giving up on the QUESTION is not the same as deciding the answer')
        assert protective.pending_order_id in executor.get_unresolved_at_ceiling()


class TestAnUnresolvedAmendWritesNoProvisionalValue:
    """The local shadow must not show a level the venue may never have taken."""

    def test_the_price_is_not_moved(self):
        mock, executor, position, protective = _protected_position()
        before = protective.entry_price
        executor.broker.adapter.set_transport_fault('modify', _FAULT)

        executor.modify_stop_order(protective.pending_order_id, new_stop_price=48500.0)
        mock.await_submit_confirmation(executor)

        assert protective.entry_price == before, (
            f'the shadow moved to {protective.entry_price} on an answer that never came — '
            f'our book would show a stop the venue is not holding')

    def test_the_provisional_values_stay_parked(self):
        mock, executor, position, protective = _protected_position()
        executor.broker.adapter.set_transport_fault('modify', _FAULT)

        executor.modify_stop_order(protective.pending_order_id, new_stop_price=48500.0)
        mock.await_submit_confirmation(executor)

        assert protective.execution_state.pending_modification is not None, (
            'discarding them would lose what was asked for, and applying them would claim '
            'it happened — parked is the only honest state')
        assert protective.execution_state.in_flight_operation is PendingOperation.PENDING_MODIFY, (
            'the one-outstanding-amend guard must stay closed until the venue answers')


def _exhaust_resolution(executor, pending) -> None:
    """Run the resolution past its ceiling without waiting for the wall clock."""
    executor.heartbeat()
    state = pending.execution_state
    state.resolution_deadline = executor.get_current_time() - timedelta(seconds=1)
    state.resolution_next_at = None
    state.resolution_in_flight = False
    executor._process_unresolved_orders()
