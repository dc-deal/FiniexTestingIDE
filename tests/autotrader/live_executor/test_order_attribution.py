"""
Live Executor — Adopting the Venue's Reference for an Order of Ours (#355 Phase 1)

The Reconciler detects, the executor writes. When a truth pull finds a resting order
carrying THIS session's client order id, the local pending that lost its submit answer
(#473) gets the venue's reference back.

Why that matters more than it sounds: a pending with no broker_ref is skipped by the poll
path on every pass, and nothing times it out — the resting-order list has no timeout at
all, and only the processor's own pending dict is checked. Meanwhile has_pending_orders()
counts it, so an algo that waits for its orders to settle waits for the rest of the
session. Restoring the reference is what ends that.

No network: MockBrokerAdapter throughout.
"""

from python.framework.trading_env.live.live_trade_executor import LiveTradeExecutor
from python.framework.types.live_types.live_execution_types import BrokerOrderStatus
from python.framework.types.live_types.reconciliation_types import BrokerOrder
from python.framework.types.trading_env_types.latency_simulator_types import PendingOperation
from python.framework.types.trading_env_types.order_types import (
    OpenOrderRequest,
    OrderDirection,
    OrderType,
)


def _broker_order(broker_ref: str, client_order_id: str) -> BrokerOrder:
    """A resting broker order as the truth pull reports it."""
    return BrokerOrder(
        broker_ref=broker_ref,
        symbol='BTCUSD',
        direction=OrderDirection.LONG,
        order_type=OrderType.LIMIT,
        lots=0.01,
        status=BrokerOrderStatus.PENDING,
        price=40000.0,
        client_order_id=client_order_id,
    )


def _resting_limit_order(executor: LiveTradeExecutor, mock) -> object:
    """
    Place a LIMIT order and hand back the confirmed local pending.

    Goes through the public submit path rather than seeding the list, so what the test
    mutates afterwards is the object the executor really tracks.
    """
    mock.feed_tick(executor, bid=39999.0, ask=40001.0)
    executor.open_order(OpenOrderRequest(
        symbol='BTCUSD',
        direction=OrderDirection.LONG,
        lots=0.01,
        order_type=OrderType.LIMIT,
        price=40000.0,
    ))
    mock.await_submit_confirmation(executor)
    active = executor.get_active_orders()
    assert len(active) == 1
    return active[0]


class TestApplyOrderAttributions:
    """The write itself: fill a missing reference, never touch one that is set."""

    def test_missing_reference_is_restored(self, executor_timeout, mock_timeout):
        pending = _resting_limit_order(executor_timeout, mock_timeout)
        # The state #473 leaves behind when the submit answer is lost: the order is kept,
        # its reference is not.
        pending.broker_ref = None
        pending.execution_state.in_flight_operation = PendingOperation.PENDING_SUBMIT

        executor_timeout.apply_order_attributions([
            (pending, _broker_order('OQ7X2A-RESTING', 'p1641_1')),
        ])

        assert pending.broker_ref == 'OQ7X2A-RESTING'
        assert pending.execution_state.in_flight_operation is PendingOperation.NONE

    def test_an_existing_reference_is_never_overwritten(self, executor_timeout, mock_timeout):
        # Overwriting a settled reference would be a CORRECTION of what we believe, and
        # correction is #349's decision, not this one's.
        pending = _resting_limit_order(executor_timeout, mock_timeout)
        original_ref = pending.broker_ref
        assert original_ref

        executor_timeout.apply_order_attributions([
            (pending, _broker_order('OTHER-REF', 'p1641_1')),
        ])

        assert pending.broker_ref == original_ref

    def test_empty_input_is_a_no_op(self, executor_timeout):
        executor_timeout.apply_order_attributions([])
        assert executor_timeout.get_active_orders() == []


class TestPollPathResumes:
    """The point of the write: the order is polled again (the #355 counter-proof)."""

    def test_a_reference_less_pending_is_never_polled_and_never_expires(
        self, executor_timeout, mock_timeout
    ):
        pending = _resting_limit_order(executor_timeout, mock_timeout)
        pending.broker_ref = None
        pending.execution_state.in_flight_operation = PendingOperation.PENDING_SUBMIT
        pending.execution_state.in_flight_query = False
        pending.execution_state.last_polled_at_ms = 0.0

        executor_timeout.heartbeat()

        # Skipped by the poller (no reference) and still in the list: this is exactly the
        # state that blocks an algo waiting on has_pending_orders().
        assert pending.execution_state.in_flight_query is False
        assert executor_timeout.has_pending_orders() is True
        assert len(executor_timeout.get_active_orders()) == 1

    def test_after_attribution_the_order_is_polled_again(self, executor_timeout, mock_timeout):
        pending = _resting_limit_order(executor_timeout, mock_timeout)
        pending.broker_ref = None
        pending.execution_state.in_flight_operation = PendingOperation.PENDING_SUBMIT
        pending.execution_state.in_flight_query = False
        pending.execution_state.last_polled_at_ms = 0.0

        executor_timeout.apply_order_attributions([
            (pending, _broker_order('OQ7X2A-RESTING', 'p1641_1')),
        ])
        executor_timeout.heartbeat()

        assert pending.execution_state.in_flight_query is True
