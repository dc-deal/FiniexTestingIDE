"""
Live Executor — Resolving a Write Whose Answer Was Lost (#487)

#473 shipped the honest half: a write nobody answered becomes UNRESOLVED rather than a fake
rejection, and a write is never retried. It left the other half open — nothing ever ASKED,
because the in-flight window was bounded by the 30 s fill timeout while the truth pull runs
on a ≥60 s cadence. The pending was dropped before anything could ask.

Three verdicts and no fourth:

    RESOLVED_RESTING    the venue names it → the reference is restored and the ordinary
                        poll path takes over. Nothing else is booked here, deliberately:
                        two booking routes for one set of facts come to disagree
    RESOLVED_ABSENT     the venue answered and named nothing, AFTER its read plane had
                        time to catch up → now, and only now, it is a genuine rejection
    UNKNOWN_AT_CEILING  we asked as long as we said we would → escalate, block new
                        ENTRIES, and never silently drop the order

What carries money here is the CANCEL and the AMEND. Booking an unresolved cancel as a
success drops an order the venue may still be holding and then releases the deferred close
beside it — the failure #503's cancel-before-close ordering exists to prevent, reached
through a transport fault instead of a race.

No network: MockBrokerAdapter with injected transport faults throughout.
"""

from datetime import timedelta

import pytest

from python.framework.testing.mock_broker_adapter import MockExecutionMode
from python.framework.testing.mock_order_execution import MockOrderExecution
from python.framework.types.live_types.reconciliation_types import BrokerOrder
from python.framework.types.live_types.live_execution_types import BrokerOrderStatus
from python.framework.types.trading_env_types.latency_simulator_types import PendingOperation
from python.framework.types.trading_env_types.order_types import (
    OpenOrderRequest,
    OrderDirection,
    OrderType,
    RejectionReason,
)

_FAULT = 'venue unreachable'


@pytest.fixture
def mock_delayed() -> MockOrderExecution:
    """DELAYED_FILL so a resting order stays resting while the test drives it."""
    return MockOrderExecution(mode=MockExecutionMode.DELAYED_FILL)


def _submit_limit(executor, mock, price: float = 40000.0):
    """Place a LIMIT and return the pending the executor is tracking for it."""
    mock.feed_tick(executor, symbol='BTCUSD')
    executor.open_order(OpenOrderRequest(
        symbol='BTCUSD', order_type=OrderType.LIMIT,
        direction=OrderDirection.LONG, lots=0.001, price=price,
    ))
    return executor._active_limit_orders[0]


def _unresolved_limit(executor, mock):
    """
    A resting order whose submit answer never arrived — the state #487 is about.

    The heartbeat at the end is not decoration: the resolution is ARMED by the scheduler,
    which runs on the tick and the heartbeat. An unanswered write is exactly the situation
    in which no tick may arrive for a while, which is why the heartbeat drives it too.
    """
    executor.broker.adapter.set_transport_fault('submit', _FAULT)
    pending = _submit_limit(executor, mock)
    mock.await_submit_confirmation(executor)
    executor.heartbeat()
    return pending


class TestTheStateUnderTestCanBeProduced:
    """
    Before anything else: the injector has to produce UNRESOLVED, not REJECTED.

    The old injector raised a plain ConnectionError, which the REST ladder classifies
    TERMINAL — so every test written against it would have asserted the wrong state while
    passing. `terminal=False` is the whole difference.
    """

    def test_a_transient_submit_fault_leaves_the_order_in_flight(
        self, executor_instant, mock_instant
    ):
        pending = _unresolved_limit(executor_instant, mock_instant)

        assert pending.broker_ref is None, 'a lost answer carries no reference'
        assert pending.execution_state.in_flight_operation is PendingOperation.PENDING_SUBMIT
        assert pending in executor_instant._active_limit_orders, (
            'the order was dropped — #473 exists to stop exactly that')

    def test_a_terminal_fault_is_a_rejection_instead(self, executor_instant, mock_instant):
        """The negative control: not every fault is an unresolved one."""
        executor_instant.broker.adapter.set_transport_fault(
            'submit', _FAULT, terminal=True)
        _submit_limit(executor_instant, mock_instant)
        mock_instant.await_submit_confirmation(executor_instant)

        assert not executor_instant._active_limit_orders, (
            'the venue answered — a refusal is a refusal and the order goes')


class TestTheAskFiresFromTheEvent:
    """Not from the reconcile cadence, which is the gap #473 left open."""

    def test_a_resolution_read_is_dispatched(self, executor_instant, mock_instant):
        pending = _unresolved_limit(executor_instant, mock_instant)
        state = pending.execution_state

        assert state.resolution_deadline is not None, (
            'nothing armed the resolution — the order would sit until the fill timeout '
            'dropped it, which is the defect this issue removes')
        assert state.resolution_attempts == 0, 'the first ask waits out query_after_ms'

    def test_the_window_is_not_the_fill_timeout(self, executor_instant, mock_instant):
        """
        An order with no answer and an order that is merely slow to fill are different
        situations, and one timer cannot mean both.
        """
        pending = _unresolved_limit(executor_instant, mock_instant)
        armed_at = executor_instant.get_current_time()
        window = (pending.execution_state.resolution_deadline - armed_at).total_seconds()

        assert window == pytest.approx(120.0), 'max_window_seconds, not order_timeout_seconds'
        assert window != executor_instant._timeout_config.order_timeout_seconds

    def test_the_fill_timer_stops_applying_to_a_market_order(self):
        """
        The interaction that would have made the whole resolution unreachable.

        Only the MARKET and CLOSE world has a fill timer at all — a resting order was never
        in `check_timeouts`' reach. And it is exactly that world the truth pull cannot see
        either, so the timeout was its ONLY exit: the pending was discarded long before a
        120 s resolution could finish. Asking for 120 s is worth nothing if something else
        drops the order at 30.

        The timeout here is ZERO, so a pending that still owned a fill timer would be gone
        on the very next pass.
        """
        mock = MockOrderExecution(mode=MockExecutionMode.INSTANT_FILL, timeout_seconds=0.0)
        executor = mock.create_executor()
        executor.broker.adapter.set_transport_fault('submit', _FAULT)
        mock.feed_tick(executor, symbol='BTCUSD')
        executor.open_order(OpenOrderRequest(
            symbol='BTCUSD', order_type=OrderType.MARKET,
            direction=OrderDirection.LONG, lots=0.001,
        ))
        mock.await_submit_confirmation(executor)
        pending = executor.get_request_processor().get_pending_orders()[0]
        assert pending.timing.timeout_at is not None, 'fixture: the fill timer was never set'

        executor.heartbeat()

        assert pending.timing.timeout_at is None, (
            'the fill timer still owns an order whose answer never came')
        assert executor.get_request_processor().get_order(
            pending.pending_order_id) is not None, (
            'the pending was discarded while the venue was still being asked about it')


class TestTheVenueNamesIt:
    """RESOLVED_RESTING — the reference is restored and nothing else is booked."""

    def test_the_reference_comes_back_and_the_order_survives(
        self, executor_instant, mock_instant
    ):
        pending = _unresolved_limit(executor_instant, mock_instant)
        adapter = executor_instant.broker.adapter
        adapter.set_transport_fault('submit', None)
        adapter.set_broker_orders([BrokerOrder(
            broker_ref='MOCK-RECOVERED', symbol='BTCUSD',
            direction=OrderDirection.LONG, order_type=OrderType.LIMIT, lots=0.001,
            status=BrokerOrderStatus.PENDING, price=40000.0,
            client_order_id=pending.client_order_id,
        )])

        _drive_resolution(executor_instant, mock_instant, pending)

        assert pending.broker_ref == 'MOCK-RECOVERED', (
            'the venue named the order and we did not take its reference back — the order '
            'stays unpollable for the rest of the session')
        assert pending in executor_instant._active_limit_orders
        assert pending.execution_state.resolution_deadline is None, 'still asking'

    def test_the_algo_is_not_told_the_order_was_rejected(
        self, executor_instant, mock_instant
    ):
        pending = _unresolved_limit(executor_instant, mock_instant)
        adapter = executor_instant.broker.adapter
        adapter.set_transport_fault('submit', None)
        adapter.set_broker_orders([BrokerOrder(
            broker_ref='MOCK-RECOVERED', symbol='BTCUSD',
            direction=OrderDirection.LONG, order_type=OrderType.LIMIT, lots=0.001,
            status=BrokerOrderStatus.PENDING, price=40000.0,
            client_order_id=pending.client_order_id,
        )])

        _drive_resolution(executor_instant, mock_instant, pending)

        rejections = [o for o in executor_instant.get_order_history() if o.is_rejected]
        assert not rejections, 'nothing was refused — the answer was merely late'


class TestTheVenueNamesNothing:
    """RESOLVED_ABSENT — but only after the settle window, never inside it."""

    def test_inside_the_settle_window_nothing_is_booked(
        self, executor_instant, mock_instant
    ):
        """
        An order accepted a moment ago may not be indexed yet, so an empty answer is not
        yet evidence. Promoting it would turn the venue's read lag into a rejection.
        """
        pending = _unresolved_limit(executor_instant, mock_instant)
        executor_instant.broker.adapter.set_transport_fault('submit', None)

        _drive_resolution(executor_instant, mock_instant, pending)

        assert pending in executor_instant._active_limit_orders, (
            'the empty answer was read as a refusal while the venue was still catching up')

    def test_after_the_settle_window_it_is_a_rejection_once(
        self, executor_instant, mock_instant
    ):
        pending = _unresolved_limit(executor_instant, mock_instant)
        executor_instant.broker.adapter.set_transport_fault('submit', None)
        # Age the write past the settle window — the same effect as time passing, without
        # making the test wait for it.
        pending.timing.last_write_at = (
            executor_instant.get_current_time() - timedelta(seconds=30))

        _drive_resolution(executor_instant, mock_instant, pending)

        assert pending not in executor_instant._active_limit_orders
        rejections = [o for o in executor_instant.get_order_history() if o.is_rejected]
        assert len(rejections) == 1, f'expected exactly one rejection, got {len(rejections)}'


class TestTheCeiling:
    """UNKNOWN_AT_CEILING — escalate, block entries, never silently drop."""

    def test_the_order_is_kept_and_entries_stop(self, executor_instant, mock_instant):
        pending = _unresolved_limit(executor_instant, mock_instant)
        _exhaust_resolution(executor_instant, pending)

        assert pending in executor_instant._active_limit_orders, (
            'the order was dropped at the ceiling — nothing measured says the venue is not '
            'holding it')
        assert pending.pending_order_id in executor_instant.get_unresolved_at_ceiling()

    def test_a_new_entry_is_refused_while_an_order_is_unaccounted_for(
        self, executor_instant, mock_instant
    ):
        pending = _unresolved_limit(executor_instant, mock_instant)
        _exhaust_resolution(executor_instant, pending)

        from python.framework.trading_env.order_guard import OrderGuard
        guard = OrderGuard()
        refusal = guard.validate(
            OpenOrderRequest(symbol='BTCUSD', order_type=OrderType.MARKET,
                             direction=OrderDirection.LONG, lots=0.001),
            executor_instant.get_current_time(),
            executor_instant.get_market_data_status(),
            unresolved_at_ceiling=executor_instant.get_unresolved_at_ceiling(),
        )

        assert refusal is not None
        assert refusal.rejection_reason == RejectionReason.UNRESOLVED_WRITE

    def test_an_empty_set_refuses_nothing(self, executor_instant, mock_instant):
        """The guard must not block the ordinary case it sits next to."""
        mock_instant.feed_tick(executor_instant, symbol='BTCUSD')
        from python.framework.trading_env.order_guard import OrderGuard
        guard = OrderGuard()
        assert guard.validate(
            OpenOrderRequest(symbol='BTCUSD', order_type=OrderType.MARKET,
                             direction=OrderDirection.LONG, lots=0.001),
            executor_instant.get_current_time(),
            executor_instant.get_market_data_status(),
            unresolved_at_ceiling=set(),
        ) is None


class TestTheTwoWorldsEndDifferentlyAtTheCeiling:
    """
    The asymmetry #473 documented, applied to the give-up.

    A RESTING order stays: the truth pull sees it every cadence and reports it, so keeping it
    costs a true `has_pending_orders()` and buys a standing record. A MARKET or CLOSE pending
    is out of that pull's reach entirely — nothing would ever look at it again — so it takes
    the disposition the timeout already defines, and stops gating the algo.

    What does NOT differ: the operator is told, and new entries stop. The latch carries "we
    still do not know" forward and is deliberately not cleared by the order leaving.
    """

    def test_a_market_pending_leaves_the_tracker_blaming_the_transport(self):
        mock = MockOrderExecution(mode=MockExecutionMode.INSTANT_FILL)
        executor = mock.create_executor()
        executor.broker.adapter.set_transport_fault('submit', _FAULT)
        mock.feed_tick(executor, symbol='BTCUSD')
        executor.open_order(OpenOrderRequest(
            symbol='BTCUSD', order_type=OrderType.MARKET,
            direction=OrderDirection.LONG, lots=0.001,
        ))
        mock.await_submit_confirmation(executor)
        executor.heartbeat()
        pending = executor.get_request_processor().get_pending_orders()[0]

        _exhaust_resolution(executor, pending)

        assert not executor.get_request_processor().has_pending_orders(), (
            'nothing will ever look at this pending again, and it gates the algo while it '
            'sits there')
        rejections = [o for o in executor.get_order_history() if o.is_rejected]
        assert len(rejections) == 1
        assert rejections[0].rejection_reason == RejectionReason.BROKER_UNREACHABLE, (
            'the venue never spoke — calling it a broker error puts our transport fault on '
            'their account')

    def test_and_the_entry_block_outlives_the_order(self):
        """
        The latch does not clear because the pending left.

        Being booked BROKER_UNREACHABLE is not being accounted for: we still do not know
        whether the venue holds that order, and that is exactly what blocks new entries.
        """
        mock = MockOrderExecution(mode=MockExecutionMode.INSTANT_FILL)
        executor = mock.create_executor()
        executor.broker.adapter.set_transport_fault('submit', _FAULT)
        mock.feed_tick(executor, symbol='BTCUSD')
        executor.open_order(OpenOrderRequest(
            symbol='BTCUSD', order_type=OrderType.MARKET,
            direction=OrderDirection.LONG, lots=0.001,
        ))
        mock.await_submit_confirmation(executor)
        executor.heartbeat()
        pending = executor.get_request_processor().get_pending_orders()[0]
        order_id = pending.pending_order_id

        _exhaust_resolution(executor, pending)
        for _ in range(3):
            executor.heartbeat()

        assert order_id in executor.get_unresolved_at_ceiling()


class TestNothingIsEverReSent:
    """A retry after a lost answer is how one intent becomes two positions (§43)."""

    def test_the_resolution_sends_no_second_order(self, executor_instant, mock_instant):
        pending = _unresolved_limit(executor_instant, mock_instant)
        adapter = executor_instant.broker.adapter
        adapter.set_transport_fault('submit', None)
        before = executor_instant.get_pending_stats()

        _exhaust_resolution(executor_instant, pending)

        assert executor_instant._orders_sent == 1, (
            f'{executor_instant._orders_sent} orders left this process for one decision')
        assert before is not None


def _drive_resolution(executor, mock, pending) -> None:
    """Let one resolution round complete: due, dispatched, answered, drained."""
    state = pending.execution_state
    state.resolution_next_at = executor.get_current_time() - timedelta(seconds=1)
    executor._process_unresolved_orders()
    mock.await_submit_confirmation(executor)


def _exhaust_resolution(executor, pending) -> None:
    """Run the resolution past its ceiling without waiting for the wall clock."""
    state = pending.execution_state
    state.resolution_deadline = executor.get_current_time() - timedelta(seconds=1)
    state.resolution_next_at = None
    state.resolution_in_flight = False
    executor._process_unresolved_orders()
