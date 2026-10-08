"""
Protective Orders — a Close Waiting Behind a Protective Order Always Ends (#503)

A close requested while the position's protective order is at the venue is PARKED until the
venue confirms that order's cancel, because a close racing a resting stop can fill twice. The
parked close ended in exactly three ways: the cancel confirmed, the close released, the close
abandoned. Every other way the protective order could stop resting left it parked for the rest
of the session — and while it waited, every new close joined it and the local stop check stood
aside, so the position could be neither closed nor protected.

    the venue refused the protective order at submission       → the close goes out
    the protective order filled in its own submit answer        → the close is dropped
    the venue never held it (resolution, cancel still parked)   → the close goes out
    a status read finds it rejected, with or without volume     → the close goes out

What still ABANDONS the close is where the stop may rest after all — a refused cancel, the
resolution's ceiling, an absence read by reference; `test_unresolved_protective_writes.py`.

No network: the protective mock with injected faults.
"""

import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from python.framework.logging.global_logger import GlobalLogger
from python.framework.testing.mock_broker_adapter import MockExecutionMode
from python.framework.testing.mock_order_execution import MockOrderExecution
from python.framework.trading_env.broker_config import BrokerConfig
from python.framework.trading_env.live.live_trade_executor import LiveTradeExecutor
from python.framework.types.live_types.live_execution_types import (
    BrokerOrderStatus,
    BrokerResponse,
)
from python.framework.types.live_types.live_request_types import QueryResponse
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.trading_env_types.broker_types import BrokerType
from python.framework.types.trading_env_types.latency_simulator_types import PendingOperation
from python.framework.types.trading_env_types.order_types import (
    OpenOrderRequest,
    OrderDirection,
    OrderStatus,
    OrderType,
)
from tests.autotrader.protective_levels.conftest import VenueHoldsProtectionMock

_SYMBOL = 'BTCUSD'
_FAULT = 'venue unreachable'


def _executor():
    """
    A live executor whose venue holds protective orders, with one tick fed.

    Returns:
        (mock, executor)
    """
    mock = MockOrderExecution(mode=MockExecutionMode.INSTANT_FILL)
    executor = LiveTradeExecutor(
        broker_config=BrokerConfig(
            BrokerType.KRAKEN_SPOT,
            VenueHoldsProtectionMock(mode=MockExecutionMode.INSTANT_FILL)),
        initial_balance=100000.0,
        account_currency='USD',
        logger=GlobalLogger('WaitingCloseSettles'),
        venue_held_protection=True,
        session_key='test',
    )
    mock.feed_tick(executor, symbol=_SYMBOL, bid=50000.0, ask=50001.0)
    return mock, executor


def _tick_leaving_the_protective_order_unanswered(executor) -> None:
    """
    One tick that fills the entry and sends its protective order — and reads no answer to it.

    The mock's own tick helper drains a second time after the tick, which would confirm the
    protective order in the same breath; the window under test is the one before that.

    Args:
        executor: The live executor
    """
    executor._request_processor.flush_outbox()
    executor.on_tick(TickData(
        timestamp=datetime.now(timezone.utc), symbol=_SYMBOL, bid=50000.0, ask=50001.0))


class _HeldStopSubmit:
    """
    Holds the venue's answer to a STOP submit until the test releases it.

    The worker thread answers a submit as soon as it is queued, so without this the protective
    order is sometimes confirmed inside the very tick that sent it, and the window a close can
    fall into — the order sent, its answer not yet read — exists only by luck of scheduling.
    """

    def __init__(self, adapter) -> None:
        self._gate = threading.Event()
        self._answer = adapter.do_request_submit
        self.reply: Optional[Dict[str, Any]] = None
        adapter.do_request_submit = self._held

    def _held(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if payload.get('order_type') == OrderType.STOP:
            self._gate.wait(timeout=10)
            if self.reply is not None:
                return self.reply
        return self._answer(payload)

    def release(self) -> None:
        """Let the venue answer the held submit."""
        self._gate.set()


def _entry_with_its_protection_in_flight(mock, executor, lots: float = 0.10):
    """
    A filled LONG whose protective order is sent and not yet answered.

    Args:
        mock: The mock order execution
        executor: The live executor
        lots: Position size

    Returns:
        (position, protective order, the held submit)
    """
    held = _HeldStopSubmit(executor.broker.adapter)
    executor.open_order(OpenOrderRequest(
        symbol=_SYMBOL, order_type=OrderType.MARKET, direction=OrderDirection.LONG,
        lots=lots, stop_loss=49000.0))
    _tick_leaving_the_protective_order_unanswered(executor)
    position = executor.get_open_positions()[0]
    protective = [p for p in executor._active_stop_orders if p.closes_position_id][0]
    assert protective.broker_ref is None, 'its submit answer has not been read yet'
    return position, protective, held


def _confirmed_protection(mock, executor, lots: float = 0.10):
    """
    A filled LONG whose stop the venue is holding.

    Returns:
        (position, protective order)
    """
    executor.open_order(OpenOrderRequest(
        symbol=_SYMBOL, order_type=OrderType.MARKET, direction=OrderDirection.LONG,
        lots=lots, stop_loss=49000.0))
    mock.feed_tick(executor, symbol=_SYMBOL, bid=50000.0, ask=50001.0)
    mock.await_submit_confirmation(executor)
    position = executor.get_open_positions()[0]
    protective = [p for p in executor._active_stop_orders if p.closes_position_id][0]
    assert position.protective_broker_ref, 'the venue confirmed it'
    return position, protective


def _close_behind_an_unanswered_cancel(mock, executor):
    """
    A close parked behind a cancel whose answer was lost, on a confirmed protective order.

    Returns:
        (position, protective order)
    """
    position, protective = _confirmed_protection(mock, executor)
    executor.broker.adapter.set_transport_fault('cancel', _FAULT)
    executor.close_position(position.position_id)
    mock.await_submit_confirmation(executor)
    executor.broker.adapter.set_transport_fault('cancel', None)
    assert position.position_id in executor._deferred_closes
    return position, protective


def _status_read(executor, protective, status, filled_lots=None, fill_price=None) -> None:
    """
    Deliver one status answer about the protective order, as the poll would.

    Args:
        executor: The live executor
        protective: The protective order
        status: What the venue says
        filled_lots: What it executed
        fill_price: At what price
    """
    executor._handle_query_response(QueryResponse(
        order_id=protective.pending_order_id,
        broker_response=BrokerResponse(
            broker_ref=protective.broker_ref, status=status,
            filled_lots=filled_lots, fill_price=fill_price,
            timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc)),
    ))


def _assert_the_close_went_out_and_filled(mock, executor, position) -> None:
    """The waiting close is no longer waiting, and the next tick closes the position."""
    assert position.position_id not in executor._deferred_closes, (
        'the close is still parked behind a cancel that will never come — every later close '
        'joins it and the local stop check stands aside for the rest of the session')
    mock.feed_tick(executor, symbol=_SYMBOL, bid=50000.0, ask=50001.0)
    assert not executor.get_open_positions(), 'the released close was not carried out'


class TestTheProtectiveOrderEndsBeforeItRests:

    def test_refused_at_submission_the_waiting_close_goes_out(self):
        mock, executor = _executor()
        position, _, held = _entry_with_its_protection_in_flight(mock, executor)
        executor.close_position(position.position_id)
        assert position.position_id in executor._deferred_closes

        executor.broker.adapter.set_transport_fault('submit', 'refused', terminal=True)
        held.release()
        mock.await_submit_confirmation(executor)
        executor.broker.adapter.set_transport_fault('submit', None)

        _assert_the_close_went_out_and_filled(mock, executor, position)

    def test_filled_in_its_own_answer_the_waiting_close_is_dropped(self):
        mock, executor = _executor()
        position, _, held = _entry_with_its_protection_in_flight(mock, executor)
        executor.close_position(position.position_id)
        held.reply = {'status': 'FILLED', 'broker_ref': 'MOCK-FILLED-STOP',
                      'filled_lots': 0.10, 'fill_price': 49000.0}

        held.release()
        mock.await_submit_confirmation(executor)

        assert not executor.get_open_positions(), 'the stop closed the position at the venue'
        assert position.position_id not in executor._deferred_closes
        assert not executor.is_pending_close(position.position_id)

    def test_never_held_while_its_cancel_was_parked_the_waiting_close_goes_out(self):
        mock, executor = _executor()
        position, protective, held = _entry_with_its_protection_in_flight(mock, executor)
        executor.broker.adapter.set_transport_fault('submit', _FAULT)
        held.release()
        mock.await_submit_confirmation(executor)              # its answer is lost
        executor.broker.adapter.set_transport_fault('submit', None)
        executor.heartbeat()                                  # the resolution is armed
        executor.close_position(position.position_id)
        assert protective.execution_state.cancel_requested, 'parked: there is no reference'
        assert protective.execution_state.in_flight_operation is PendingOperation.PENDING_SUBMIT

        # The venue holds nothing under our key, after its read plane had time to settle
        protective.timing.last_write_at = executor.get_current_time() - timedelta(seconds=30)
        protective.execution_state.resolution_next_at = (
            executor.get_current_time() - timedelta(seconds=1))
        executor._process_unresolved_orders()
        mock.await_submit_confirmation(executor)

        assert [o.status for o in executor.get_order_history()
                if o.order_id == protective.pending_order_id
                and o.status is not OrderStatus.PENDING] == [OrderStatus.UNDELIVERED]
        _assert_the_close_went_out_and_filled(mock, executor, position)


class TestTheVenueEndsTheProtectiveOrderAfterItRested:

    def test_rejected_without_volume_the_waiting_close_goes_out(self):
        mock, executor = _executor()
        position, protective = _close_behind_an_unanswered_cancel(mock, executor)

        _status_read(executor, protective, BrokerOrderStatus.REJECTED)

        _assert_the_close_went_out_and_filled(mock, executor, position)

    def test_rejected_after_closing_part_the_rest_goes_out(self):
        mock, executor = _executor()
        position, protective = _close_behind_an_unanswered_cancel(mock, executor)

        _status_read(executor, protective, BrokerOrderStatus.REJECTED,
                     filled_lots=0.04, fill_price=49000.0)

        assert executor.get_open_positions()[0].lots == 0.06, 'the executed part is booked'
        _assert_the_close_went_out_and_filled(mock, executor, position)
