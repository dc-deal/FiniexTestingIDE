"""
FiniexTestingIDE - Order Events in a Live Session (#362)

What a live session writes into the order-event stream, against the mock venue: an answer that
carries the venue's reference is the acceptance, a cancel asked for before the reference arrived
is parked and sent once it does, a lost answer is `unresolved` until the asking settles it, an
order a previous session left resting is adopted, and a venue that ends an order after part of
it executed leaves a fill and then the ending.

Live keeps two times on every step — when it happened on the session's clock, and when this
process saw it on the machine's clock — and measures the latency of an answered submission.
"""

from datetime import datetime, timedelta, timezone

import pytest

from python.framework.logging.global_logger import GlobalLogger
from python.framework.reporting.builders.pending_orders_report_builder import pending_orders_row
from python.framework.testing.mock_broker_adapter import MockExecutionMode
from python.framework.testing.mock_order_execution import MockOrderExecution
from python.framework.trading_env.broker_config import BrokerConfig
from python.framework.trading_env.live.live_trade_executor import LiveTradeExecutor
from python.framework.types.live_types.live_execution_types import (
    BrokerOrderStatus,
    BrokerResponse,
)
from python.framework.types.live_types.live_request_types import QueryResponse
from python.framework.types.live_types.reconciliation_types import BrokerOrder
from python.framework.types.trading_env_types.broker_types import BrokerType
from python.framework.types.trading_env_types.order_event_types import (
    OrderEventType,
    OrderOperation,
)
from python.framework.types.trading_env_types.order_types import (
    OpenOrderRequest,
    OrderAction,
    OrderDirection,
    OrderEndReason,
    OrderInitiator,
    OrderType,
)
from tests.autotrader.protective_levels.conftest import VenueHoldsProtectionMock
from tests.framework.order_events.conftest import (
    ASK,
    BID,
    limit_order,
    live_session,
    market_order,
    order_steps,
    record_events,
)

ACCOUNT_MODELS = pytest.mark.parametrize('spot_mode', [False, True], ids=['margin', 'spot'])
_FAULT = 'venue unreachable'


def _drive_resolution(executor: LiveTradeExecutor, mock: MockOrderExecution, pending) -> None:
    """Let one resolution round complete: due, dispatched, answered, drained."""
    pending.execution_state.resolution_next_at = executor.get_current_time() - timedelta(seconds=1)
    executor._process_unresolved_orders()
    mock.await_submit_confirmation(executor)


class TestAMarketOrder:
    """The venue answers with its reference first; the fill shows up later."""

    @ACCOUNT_MODELS
    def test_it_is_submitted_accepted_and_filled(self, spot_mode):
        mock, executor, events = live_session(spot_mode=spot_mode)

        executor.open_order(market_order())
        mock.await_submit_confirmation(executor)              # the answer: a reference
        mock.feed_tick(executor, bid=BID, ask=ASK)            # the poll: a fill

        assert order_steps(events) == [['submitted', 'accepted', 'filled']]

    def test_the_acceptance_is_measured_and_every_step_has_two_times(self):
        mock, executor, events = live_session()

        executor.open_order(market_order())
        mock.await_submit_confirmation(executor)
        mock.feed_tick(executor, bid=BID, ask=ASK)

        accepted = [e for e in events if e.event_type is OrderEventType.ACCEPTED][0]
        assert accepted.in_flight_ms is not None and accepted.in_flight_ms >= 0.0
        assert all(e.ts_init is not None and e.event_time is not None for e in events)

    def test_a_fill_in_the_answer_itself_is_still_accepted_first(self):
        mock, executor, events = live_session(mode=MockExecutionMode.INSTANT_FILL)

        executor.open_order(market_order())
        mock.await_submit_confirmation(executor)

        assert order_steps(events) == [['submitted', 'accepted', 'filled']]

    def test_a_refusal_answers_the_submission_with_the_venues_reason(self):
        mock, executor, events = live_session(mode=MockExecutionMode.REJECT_ALL)

        executor.open_order(market_order())
        mock.await_submit_confirmation(executor)

        assert order_steps(events) == [['submitted', 'rejected']]
        rejected = events[-1]
        assert rejected.venue_reason, 'the venue gave a reason and it is passed on as it came'
        assert rejected.in_flight_ms is not None

    def test_a_denial_was_never_submitted(self):
        mock, executor, events = live_session()

        executor.open_order(market_order(lots=1e-9))

        assert [(e.event_type, e.submitted_seq) for e in events] == [
            (OrderEventType.DENIED, None)]


class TestARestingOrder:
    """Accepted when the answer names it, then every request and its answer is a step."""

    def test_a_strategy_cancel_is_asked_for_then_carried_out(self):
        mock, executor, events = live_session()
        order_id = executor.open_order(limit_order()).order_id
        mock.await_submit_confirmation(executor)

        assert executor.cancel_limit_order(order_id)
        mock.await_submit_confirmation(executor)

        assert order_steps(events) == [['submitted', 'accepted', 'cancel_requested', 'cancelled']]
        requested, cancelled = events[-2:]
        assert (requested.initiator, requested.end_reason) == (
            OrderInitiator.STRATEGY, OrderEndReason.CANCEL_REQUESTED)
        assert (cancelled.initiator, cancelled.end_reason) == (
            OrderInitiator.STRATEGY, OrderEndReason.CANCEL_REQUESTED)

    def test_a_cancel_before_the_reference_is_parked_and_sent_on_the_answer(self):
        mock, executor, events = live_session()
        order_id = executor.open_order(limit_order()).order_id

        assert executor.cancel_limit_order(order_id)          # no reference yet
        mock.await_submit_confirmation(executor)              # the answer, then the cancel
        mock.await_submit_confirmation(executor)              # the cancel's answer

        assert order_steps(events) == [[
            'submitted', 'cancel_deferred', 'accepted', 'cancel_requested', 'cancelled']]

    def test_an_amend_is_asked_for_then_carried_out(self):
        mock, executor, events = live_session()
        order_id = executor.open_order(limit_order()).order_id
        mock.await_submit_confirmation(executor)

        assert executor.modify_limit_order(order_id, new_price=41000.0).success
        mock.await_submit_confirmation(executor)

        assert order_steps(events) == [['submitted', 'accepted', 'modify_requested', 'modified']]
        assert [e.limit_price for e in events[-2:]] == [41000.0, 41000.0]

    def test_a_local_refusal_of_an_amend_writes_nothing(self):
        mock, executor, events = live_session()
        order_id = executor.open_order(limit_order()).order_id

        result = executor.modify_limit_order(order_id, new_price=41000.0)

        assert not result.success, 'not confirmed yet — refused here'
        assert order_steps(events) == [['submitted']], 'the answer is the return value'

    def test_the_venue_ending_it_after_a_partial_fill_is_a_fill_then_the_ending(self):
        """
        One row, two steps: the order history keeps the fill of what executed; the stream
        also keeps that the rest was ended rather than filled.
        """
        mock, executor, events = live_session(mode=MockExecutionMode.TIMEOUT)
        order_id = executor.open_order(limit_order()).order_id
        mock.await_submit_confirmation(executor)
        resting = executor.get_active_orders()[0]

        executor._handle_query_response(QueryResponse(
            order_id=order_id,
            broker_response=BrokerResponse(
                broker_ref=resting.broker_ref, status=BrokerOrderStatus.CANCELLED,
                filled_lots=0.004, fill_price=40000.0,
                timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc)),
        ))

        assert order_steps(events) == [['submitted', 'accepted', 'partially_filled', 'cancelled']]
        partial, ended = events[-2:]
        assert partial.lots == pytest.approx(0.004)
        assert (ended.lots, ended.cum_lots, ended.initiator) == (
            None, pytest.approx(0.004), OrderInitiator.VENUE)

    def test_the_session_end_cancels_it_and_says_so(self):
        mock, executor, events = live_session()
        executor.open_order(limit_order())
        mock.await_submit_confirmation(executor)

        executor.finish_remaining_orders(cancel_orders=True)

        assert order_steps(events) == [['submitted', 'accepted', 'cancel_requested', 'cancelled']]
        assert {e.end_reason for e in events[-2:]} == {OrderEndReason.SESSION_END}


class TestALostAnswer:
    """`unresolved` until the asking settles it — by naming the order, or by its absence."""

    def _unresolved_limit(self, mock, executor):
        """A resting order whose submit answer never arrived."""
        executor.broker.adapter.set_transport_fault('submit', _FAULT)
        executor.open_order(limit_order())
        pending = executor._active_limit_orders[0]
        mock.await_submit_confirmation(executor)
        executor.heartbeat()
        executor.broker.adapter.set_transport_fault('submit', None)
        return pending

    def test_the_venue_naming_it_resolves_it_and_accepts_it_late(self):
        mock, executor, events = live_session(mode=MockExecutionMode.INSTANT_FILL)
        pending = self._unresolved_limit(mock, executor)
        executor.broker.adapter.set_broker_orders([BrokerOrder(
            broker_ref='MOCK-RECOVERED', symbol='BTCUSD', direction=OrderDirection.LONG,
            order_type=OrderType.LIMIT, lots=0.01, status=BrokerOrderStatus.PENDING,
            price=40000.0, client_order_id=pending.client_order_id,
        )])

        _drive_resolution(executor, mock, pending)

        assert order_steps(events) == [['submitted', 'unresolved', 'resolved', 'accepted']]
        unresolved, resolved, accepted = events[1:]
        assert (unresolved.lost_request, resolved.lost_request) == (
            OrderOperation.SUBMIT, OrderOperation.SUBMIT)
        assert accepted.in_flight_ms is None, 'that span measures the asking, not the venue'

    def test_the_venue_never_having_it_ends_it_undelivered(self):
        mock, executor, events = live_session(mode=MockExecutionMode.INSTANT_FILL)
        pending = self._unresolved_limit(mock, executor)
        pending.timing.last_write_at = executor.get_current_time() - timedelta(seconds=30)

        _drive_resolution(executor, mock, pending)

        assert order_steps(events) == [['submitted', 'unresolved', 'undelivered']]

    def test_the_truth_pull_naming_it_is_a_resolution_too(self):
        mock, executor, events = live_session(mode=MockExecutionMode.INSTANT_FILL)
        pending = self._unresolved_limit(mock, executor)

        executor.apply_order_attributions([(pending, BrokerOrder(
            broker_ref='MOCK-ATTRIBUTED', symbol='BTCUSD', direction=OrderDirection.LONG,
            order_type=OrderType.LIMIT, lots=0.01, status=BrokerOrderStatus.PENDING,
            price=40000.0, client_order_id=pending.client_order_id,
        ))])

        assert order_steps(events) == [['submitted', 'unresolved', 'resolved', 'accepted']]
        assert events[-1].broker_ref == 'MOCK-ATTRIBUTED'


class TestAnAdoptedOrder:
    """Sent by a previous session: adopted, never accepted a second time."""

    def test_it_is_adopted_and_its_fill_follows_without_a_second_acceptance(self):
        mock, executor, events = live_session(mode=MockExecutionMode.TIMEOUT)
        executor.adopt_resting_orders([('pos_btcusd_7', BrokerOrder(
            broker_ref='MOCK-ADOPTED', symbol='BTCUSD', direction=OrderDirection.LONG,
            order_type=OrderType.LIMIT, lots=0.01, status=BrokerOrderStatus.PENDING,
            price=40000.0, client_order_id='pmock_7',
        ))])

        executor._handle_query_response(QueryResponse(
            order_id='pos_btcusd_7',
            broker_response=BrokerResponse(
                broker_ref='MOCK-ADOPTED', status=BrokerOrderStatus.FILLED,
                filled_lots=0.01, fill_price=40000.0,
                timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc)),
        ))

        assert order_steps(events) == [['adopted', 'filled']]
        stats = executor.get_execution_stats()
        assert (stats.orders_submitted, stats.orders_adopted, stats.orders_executed) == (0, 1, 1)


class TestAProtectiveOrder:
    """Placed after the entry fills, cancelled before the close is sent — steps of its own."""

    def test_a_close_cancels_the_protection_first_and_then_goes_out(self):
        mock = MockOrderExecution(mode=MockExecutionMode.INSTANT_FILL)
        executor = LiveTradeExecutor(
            broker_config=BrokerConfig(
                BrokerType.KRAKEN_SPOT,
                VenueHoldsProtectionMock(mode=MockExecutionMode.INSTANT_FILL)),
            initial_balance=100000.0,
            account_currency='USD',
            logger=GlobalLogger('OrderEventsProtective'),
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

        executor.close_position(position.position_id)
        mock.await_submit_confirmation(executor)              # the stop's cancel
        mock.await_submit_confirmation(executor)              # the close

        entry, protective, close = order_steps(events)
        assert entry == ['submitted', 'accepted', 'filled']
        assert protective == ['submitted', 'accepted', 'cancel_requested', 'cancelled']
        assert close == ['submitted', 'accepted', 'filled']
        protective_events = [e for e in events if e.order_type is OrderType.STOP]
        assert {(e.action, e.position_id, e.direction) for e in protective_events} == {
            (OrderAction.CLOSE, position.position_id, OrderDirection.LONG)}, (
            'a protective order states the position it protects, and that position\'s side')
        assert {e.end_reason for e in protective_events if e.end_reason} == {
            OrderEndReason.PROTECTION_RELEASED}


class TestACloseWhosePositionIsGone:
    """
    A close's answer can arrive after something else took its position — another close, a stop
    the venue held. Its steps still name the position's side and size: both travel on the close
    from the moment it is registered, not from a position that may be gone by then.
    """

    @ACCOUNT_MODELS
    def test_its_steps_still_name_the_side_and_the_size(self, spot_mode):
        mock, executor, events = live_session(spot_mode=spot_mode)
        executor.open_order(market_order())
        mock.await_submit_confirmation(executor)
        mock.feed_tick(executor, bid=BID, ask=ASK)
        position = executor.get_open_positions()[0]

        executor.close_position(position.position_id)
        executor.portfolio.close_position_portfolio(   # taken before the venue answers
            position.position_id, exit_price=BID, exit_tick_value=1.0, exit_tick_index=0)
        mock.await_submit_confirmation(executor)
        mock.feed_tick(executor, bid=BID, ask=ASK)

        close_steps = [e for e in events if e.action is OrderAction.CLOSE]
        assert [e.event_type for e in close_steps][:2] == [
            OrderEventType.SUBMITTED, OrderEventType.ACCEPTED]
        assert {(e.direction, e.lots) for e in close_steps} == {(OrderDirection.LONG, 0.01)}, (
            [(e.event_type.value, e.direction, e.lots) for e in close_steps])


class TestTheStreamItself:
    """Ordered by `seq`, joined by `submitted_seq` — and an answer for a gone order is explained."""

    def test_seq_is_strictly_increasing_across_orders(self):
        mock, executor, events = live_session()
        executor.open_order(market_order())
        order_id = executor.open_order(limit_order()).order_id
        mock.await_submit_confirmation(executor)
        executor.cancel_limit_order(order_id)
        mock.await_submit_confirmation(executor)
        mock.feed_tick(executor, bid=BID, ask=ASK)

        assert [e.seq for e in events] == list(range(1, len(events) + 1))
        submissions = {e.seq for e in events if e.event_type is OrderEventType.SUBMITTED}
        assert {e.submitted_seq for e in events} <= submissions

    def test_a_late_answer_names_how_its_order_ended(self, capsys):
        mock, executor, events = live_session()
        order_id = executor.open_order(limit_order()).order_id
        mock.await_submit_confirmation(executor)
        ref = next(p.broker_ref for p in executor.get_active_orders()
                   if p.pending_order_id == order_id)
        executor.cancel_limit_order(order_id)
        mock.await_submit_confirmation(executor)
        cancelled = events[-1]
        capsys.readouterr()

        # A status read that was on its way when the order ended, drained afterwards
        executor._handle_query_response(QueryResponse(
            order_id=order_id, broker_response=BrokerResponse(
                broker_ref=ref, status=BrokerOrderStatus.CANCELLED,
                timestamp=datetime.now(timezone.utc))))

        printed = capsys.readouterr().out
        assert f'it had already ended as cancelled (seq {cancelled.seq})' in printed
        assert executor.describe_recent_ending('pos_btcusd_404') == ''


class TestTheSessionsPendingCounters:
    """A live session's in-flight counters come from the events it recorded, as a backtest's."""

    def test_each_way_out_of_flight_is_counted_and_they_add_up(self):
        mock, executor, events = live_session()
        executor.open_order(market_order())                                    # accepted
        mock.await_submit_confirmation(executor)
        executor.broker.adapter.set_transport_fault('submit', 'refused', terminal=True)
        executor.open_order(market_order())                                    # rejected
        mock.await_submit_confirmation(executor)
        executor.broker.adapter.set_transport_fault('submit', _FAULT)
        executor.open_order(market_order())                                    # never confirmed
        mock.await_submit_confirmation(executor)
        executor.heartbeat()
        lost = next(p for p in executor.get_request_processor().get_pending_orders()
                    if p.broker_ref is None)
        lost.execution_state.resolution_deadline = (
            executor.get_current_time() - timedelta(seconds=1))
        lost.execution_state.resolution_next_at = None
        lost.execution_state.resolution_in_flight = False
        executor._process_unresolved_orders()                                 # the ceiling

        row = pending_orders_row(
            'session', 'BTCUSD', events, executor.get_active_orders_snapshot())

        assert (row.total_submitted, row.total_accepted, row.total_rejected,
                row.total_never_confirmed, row.total_expired) == (3, 1, 1, 1, 0)
        assert row.in_flight_count == 2, 'the acceptance and the refusal were answers'
        assert [o.event_type for o in row.never_confirmed_orders] == [
            OrderEventType.UNACCOUNTED]
