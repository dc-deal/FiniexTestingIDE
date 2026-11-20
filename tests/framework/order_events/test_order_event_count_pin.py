"""
The order-event record counted against the executor's own counts (#512).

The divergence report checks each sealed day of a live session by folding the day's order-event
record through `ORDER_EVENT_COUNT_COLUMN` and comparing every column with the day's ledger row. That
is only sound if the fold counts exactly what the executor counted. These tests drive the live
executor through every way an order lives and ends, in both account models, and hold the fold to
the executor's counts — the step after a partial fill included, which is recorded and never
counted. One path is recorded without being counted and carries no mark: a close the venue
executed for a position no longer held here. It is pinned as the one known difference.
"""

from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Tuple

import pytest

from python.framework.reporting.builders.divergence.live_trading_day_builder import (
    count_day_order_events,
)
from python.framework.reporting.io.order_event_stream_io import order_event_row
from python.framework.testing.mock_broker_adapter import MockExecutionMode
from python.framework.testing.mock_order_execution import MockOrderExecution
from python.framework.trading_env.live.live_trade_executor import LiveTradeExecutor
from python.framework.types.divergence_capture_types import UNCOMPARED_COUNT_COLUMNS
from python.framework.types.live_types.live_execution_types import (
    BrokerOrderStatus,
    BrokerResponse,
)
from python.framework.types.live_types.live_request_types import QueryResponse
from python.framework.types.live_types.reconciliation_types import BrokerOrder
from python.framework.types.trading_env_types.order_event_types import OrderEvent
from python.framework.types.trading_env_types.order_types import OrderDirection, OrderType
from python.framework.types.trading_env_types.trading_env_stats_types import EXECUTION_COUNT_FIELDS
from tests.framework.order_events.conftest import (
    ASK,
    BID,
    limit_order,
    live_session,
    market_order,
)

ACCOUNT_MODELS = pytest.mark.parametrize('spot_mode', [False, True], ids=['margin', 'spot'])
_COMPARED = [column for column in EXECUTION_COUNT_FIELDS if column not in UNCOMPARED_COUNT_COLUMNS]
_ANSWERED_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)

Session = Tuple[MockOrderExecution, LiveTradeExecutor, List[OrderEvent]]


def _fill_and_close(spot_mode: bool) -> Session:
    """A market order filled, its position closed by a second one."""
    mock, executor, events = live_session(spot_mode=spot_mode)
    executor.open_order(market_order())
    mock.await_submit_confirmation(executor)
    mock.feed_tick(executor, bid=BID, ask=ASK)
    executor.close_position(executor.get_open_positions()[0].position_id)
    mock.await_submit_confirmation(executor)
    mock.feed_tick(executor, bid=BID, ask=ASK)
    return mock, executor, events


def _refusal(spot_mode: bool) -> Session:
    """The venue refuses the order."""
    mock, executor, events = live_session(mode=MockExecutionMode.REJECT_ALL, spot_mode=spot_mode)
    executor.open_order(market_order())
    mock.await_submit_confirmation(executor)
    return mock, executor, events


def _denial(spot_mode: bool) -> Session:
    """Refused here, never sent."""
    mock, executor, events = live_session(spot_mode=spot_mode)
    executor.open_order(market_order(lots=1e-9))
    return mock, executor, events


def _strategy_cancel(spot_mode: bool) -> Session:
    """A resting order the strategy cancels."""
    mock, executor, events = live_session(spot_mode=spot_mode)
    order_id = executor.open_order(limit_order()).order_id
    mock.await_submit_confirmation(executor)
    assert executor.cancel_limit_order(order_id)
    mock.await_submit_confirmation(executor)
    return mock, executor, events


def _partial_fill_then_venue_end(spot_mode: bool) -> Session:
    """The venue ends a resting order after part of it executed."""
    mock, executor, events = live_session(mode=MockExecutionMode.TIMEOUT, spot_mode=spot_mode)
    order_id = executor.open_order(limit_order()).order_id
    mock.await_submit_confirmation(executor)
    resting = executor.get_active_orders()[0]
    executor._handle_query_response(QueryResponse(
        order_id=order_id, broker_response=BrokerResponse(
            broker_ref=resting.broker_ref, status=BrokerOrderStatus.CANCELLED,
            filled_lots=0.004, fill_price=40000.0, timestamp=_ANSWERED_AT)))
    return mock, executor, events


def _adoption_then_fill(spot_mode: bool) -> Session:
    """An order a previous session sent, adopted at boot and filled after it."""
    mock, executor, events = live_session(mode=MockExecutionMode.TIMEOUT, spot_mode=spot_mode)
    executor.adopt_resting_orders([('pos_btcusd_7', BrokerOrder(
        broker_ref='MOCK-ADOPTED', symbol='BTCUSD', direction=OrderDirection.LONG,
        order_type=OrderType.LIMIT, lots=0.01, status=BrokerOrderStatus.PENDING,
        price=40000.0, client_order_id='pmock_7'))])
    executor._handle_query_response(QueryResponse(
        order_id='pos_btcusd_7', broker_response=BrokerResponse(
            broker_ref='MOCK-ADOPTED', status=BrokerOrderStatus.FILLED, filled_lots=0.01,
            fill_price=40000.0, timestamp=_ANSWERED_AT)))
    return mock, executor, events


def _session_end_cancel(spot_mode: bool) -> Session:
    """The session's end cancels a resting order."""
    mock, executor, events = live_session(spot_mode=spot_mode)
    executor.open_order(limit_order())
    mock.await_submit_confirmation(executor)
    executor.finish_remaining_orders(cancel_orders=True)
    return mock, executor, events


def _lost_answer_never_received(spot_mode: bool) -> Session:
    """A submission whose answer was lost, and the venue never had the order."""
    mock, executor, events = live_session(mode=MockExecutionMode.INSTANT_FILL, spot_mode=spot_mode)
    executor.broker.adapter.set_transport_fault('submit', 'venue unreachable')
    executor.open_order(limit_order())
    pending = executor._active_limit_orders[0]
    mock.await_submit_confirmation(executor)
    executor.heartbeat()
    executor.broker.adapter.set_transport_fault('submit', None)
    pending.timing.last_write_at = executor.get_current_time() - timedelta(seconds=30)
    pending.execution_state.resolution_next_at = executor.get_current_time() - timedelta(seconds=1)
    executor._process_unresolved_orders()
    mock.await_submit_confirmation(executor)
    return mock, executor, events


_LIVES: Dict[str, Callable[[bool], Session]] = {
    'fill_and_close': _fill_and_close,
    'refusal': _refusal,
    'denial': _denial,
    'strategy_cancel': _strategy_cancel,
    'partial_fill_then_venue_end': _partial_fill_then_venue_end,
    'adoption_then_fill': _adoption_then_fill,
    'session_end_cancel': _session_end_cancel,
    'lost_answer_never_received': _lost_answer_never_received,
}


def _recorded_and_counted(session: Session) -> Tuple[Dict[str, int], Dict[str, int]]:
    """The fold of what was recorded, and the executor's own counts, per compared column."""
    _, executor, events = session
    recorded = count_day_order_events([order_event_row(event, 'my_session') for event in events])
    stats = executor.get_execution_stats()
    return ({column: recorded[column] for column in _COMPARED},
            {column: getattr(stats, column) for column in _COMPARED})


@ACCOUNT_MODELS
@pytest.mark.parametrize('life', sorted(_LIVES))
def test_the_record_counts_what_the_executor_counted(life, spot_mode):
    recorded, counted = _recorded_and_counted(_LIVES[life](spot_mode))
    assert recorded == counted
    assert any(counted.values()), 'the life counted nothing, so it proves nothing'


@ACCOUNT_MODELS
def test_the_step_after_a_partial_fill_is_recorded_and_not_counted(spot_mode):
    _, _, events = session = _partial_fill_then_venue_end(spot_mode)
    recorded, counted = _recorded_and_counted(session)
    assert [event.event_type.value for event in events][-2:] == ['partially_filled', 'cancelled']
    assert recorded['orders_cancelled'] == counted['orders_cancelled'] == 0
    assert recorded['orders_executed'] == counted['orders_executed'] == 1


@ACCOUNT_MODELS
def test_a_close_for_a_position_no_longer_held_is_the_one_known_difference(spot_mode):
    """Recorded as the venue's fill, booked nowhere: one execution more recorded than counted."""
    mock, executor, events = live_session(spot_mode=spot_mode)
    executor.open_order(market_order())
    mock.await_submit_confirmation(executor)
    mock.feed_tick(executor, bid=BID, ask=ASK)
    position = executor.get_open_positions()[0]
    executor.close_position(position.position_id)
    executor.portfolio.close_position_portfolio(
        position.position_id, exit_price=BID, exit_tick_value=1.0, exit_tick_index=0)
    mock.await_submit_confirmation(executor)
    mock.feed_tick(executor, bid=BID, ask=ASK)

    recorded, counted = _recorded_and_counted((mock, executor, events))
    differs = {column for column in _COMPARED if recorded[column] != counted[column]}
    assert differs == {'orders_executed'}
    assert recorded['orders_executed'] - counted['orders_executed'] == 1
