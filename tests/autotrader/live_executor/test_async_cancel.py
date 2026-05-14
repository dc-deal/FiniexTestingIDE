"""
Live Async Cancel Path — Regression Tests (#318)

Locks down the shape of the async cancel lifecycle:
- cancel_limit_order returns True (scheduled) immediately, NOT False-on-not-yet-cancelled
- target.in_flight_operation = PENDING_CANCEL during the in-flight window
- drain_inbox removes the order from _active_limit_orders on success
- Busy / not-confirmed / not-found / unsupported reject paths
- Cancel-during-fill race handled (logged, in_flight cleared)
"""

from python.framework.testing.mock_adapter import MockExecutionMode
from python.framework.types.trading_env_types.latency_simulator_types import PendingOperation
from python.framework.types.trading_env_types.order_types import (
    OpenOrderRequest,
    OrderDirection,
    OrderType,
)


def _submit_limit_and_confirm(mock, executor, price=49000.0, lots=0.001):
    """Helper: submit a LIMIT order and confirm broker_ref via drain."""
    mock.feed_tick(executor, bid=49999.0, ask=50001.0)
    result = executor.open_order(OpenOrderRequest(
        symbol="BTCUSD", order_type=OrderType.LIMIT,
        direction=OrderDirection.LONG, lots=lots, price=price,
    ))
    mock.await_submit_confirmation(executor)
    return result.order_id


class TestCancelLimitOrderAsyncLifecycle:
    """The async cancel lifecycle: True return → drain removes → state cleared."""

    def test_cancel_returns_true_when_scheduled(self, mock_delayed, executor_delayed):
        """cancel_limit_order returns True for a valid, confirmed, idle order."""
        order_id = _submit_limit_and_confirm(mock_delayed, executor_delayed)

        scheduled = executor_delayed.cancel_limit_order(order_id=order_id)
        assert scheduled is True

    def test_in_flight_operation_set_during_window(self, mock_delayed, executor_delayed):
        """After cancel_limit_order schedule, target.in_flight_operation == PENDING_CANCEL."""
        order_id = _submit_limit_and_confirm(mock_delayed, executor_delayed)

        executor_delayed.cancel_limit_order(order_id=order_id)

        target = next(p for p in executor_delayed._active_limit_orders
                      if p.pending_order_id == order_id)
        assert target.in_flight_operation == PendingOperation.PENDING_CANCEL

    def test_order_removed_from_active_after_drain(self, mock_delayed, executor_delayed):
        """Next feed_tick drains CancelResponse; order removed from _active_limit_orders."""
        order_id = _submit_limit_and_confirm(mock_delayed, executor_delayed)
        before = len(executor_delayed._active_limit_orders)

        executor_delayed.cancel_limit_order(order_id=order_id)
        mock_delayed.feed_tick(executor_delayed, bid=49999.0, ask=50001.0)

        after = len(executor_delayed._active_limit_orders)
        assert after == before - 1
        # Order is gone from the active list
        assert not any(p.pending_order_id == order_id
                       for p in executor_delayed._active_limit_orders)


class TestCancelLimitOrderBusy:
    """One in-flight operation per order — second call rejected."""

    def test_second_cancel_returns_false(self, mock_delayed, executor_delayed):
        """Second cancel while first is in-flight → returns False (busy)."""
        order_id = _submit_limit_and_confirm(mock_delayed, executor_delayed)

        first = executor_delayed.cancel_limit_order(order_id=order_id)
        assert first is True

        second = executor_delayed.cancel_limit_order(order_id=order_id)
        assert second is False

    def test_cancel_during_pending_modify_returns_false(self, mock_delayed, executor_delayed):
        """Cancel on an order with PENDING_MODIFY in flight → False (busy)."""
        order_id = _submit_limit_and_confirm(mock_delayed, executor_delayed)

        mod_result = executor_delayed.modify_limit_order(order_id=order_id, new_price=51000.0)
        assert mod_result.success is True

        cancel_scheduled = executor_delayed.cancel_limit_order(order_id=order_id)
        assert cancel_scheduled is False


class TestCancelLimitOrderNotConfirmed:
    """Option A: cancel on submit-in-flight order returns False (broker_ref=None)."""

    def test_cancel_before_broker_ref_confirmed(self, mock_delayed, executor_delayed):
        """Order in _active_limit_orders with broker_ref=None → False."""
        mock_delayed.feed_tick(executor_delayed, bid=49999.0, ask=50001.0)
        result = executor_delayed.open_order(OpenOrderRequest(
            symbol="BTCUSD", order_type=OrderType.LIMIT,
            direction=OrderDirection.LONG, lots=0.001, price=49000.0,
        ))
        order_id = result.order_id
        # Do NOT call await_submit_confirmation — broker_ref still None

        scheduled = executor_delayed.cancel_limit_order(order_id=order_id)
        assert scheduled is False


class TestCancelLimitOrderNotFound:
    """Unknown order_id returns False."""

    def test_cancel_nonexistent_order(self, mock_delayed, executor_delayed):
        """cancel_limit_order on unknown order_id → False."""
        mock_delayed.feed_tick(executor_delayed, bid=49999.0, ask=50001.0)

        scheduled = executor_delayed.cancel_limit_order(order_id="NONEXISTENT")
        assert scheduled is False


class TestCancelStopOrderCapabilityGate:
    """cancel_stop_order rejected when adapter doesn't declare STOP capability."""

    def test_cancel_stop_order_returns_false_for_kraken_profile(self, mock_delayed, executor_delayed):
        """Mock adapter declares stop_orders=False → cancel_stop_order returns False."""
        scheduled = executor_delayed.cancel_stop_order(order_id="anything")
        assert scheduled is False


class TestCancelHasInFlight:
    """has_in_flight_operation reflects cancel state correctly."""

    def test_has_in_flight_operation_after_cancel_schedule(self, mock_delayed, executor_delayed):
        """has_in_flight_operation returns True during cancel window."""
        order_id = _submit_limit_and_confirm(mock_delayed, executor_delayed)

        executor_delayed.cancel_limit_order(order_id=order_id)
        assert executor_delayed.has_in_flight_operation(order_id) is True
