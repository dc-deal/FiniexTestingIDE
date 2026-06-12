"""
Live Async Modify Path — Regression Tests (#318)

Locks down the shape of the async modify lifecycle introduced by #318:
- modify_limit_order returns PENDING with status=PENDING immediately
- target.execution_state.in_flight_operation = PENDING_MODIFY during the in-flight window
- drain_inbox applies the modification on next tick (entry_price, SL, TP)
- broker_ref swap (Kraken EditOrder semantic) is handled in drain
- Busy / not-confirmed / not-found / unsupported reject paths

These tests assert the SHAPE of the async lifecycle. test_live_executor_modify.py
already covers outcomes (success/rejection counts); this file specifically
asserts the in-flight state transitions so a regression to sync-modify
cannot slip through outcome-only assertions.
"""

from python.framework.testing.mock_broker_adapter import MockExecutionMode
from python.framework.types.trading_env_types.latency_simulator_types import PendingOperation
from python.framework.types.trading_env_types.order_types import (
    ModificationRejectionReason,
    ModificationStatus,
    OpenOrderRequest,
    OrderDirection,
    OrderStatus,
    OrderType,
)


def _submit_limit_and_confirm(mock, executor, price=49000.0, lots=0.001):
    """Helper: submit a LIMIT order and confirm broker_ref via drain (no Phase-2 fill)."""
    mock.feed_tick(executor, bid=49999.0, ask=50001.0)
    result = executor.open_order(OpenOrderRequest(
        symbol="BTCUSD", order_type=OrderType.LIMIT,
        direction=OrderDirection.LONG, lots=lots, price=price,
    ))
    mock.await_submit_confirmation(executor)
    return result.order_id


class TestModifyLimitOrderAsyncLifecycle:
    """The async modify lifecycle: PENDING return → drain applies → state cleared."""

    def test_modify_returns_pending_initially(self, mock_delayed, executor_delayed):
        """modify_limit_order returns success=True with status=PENDING."""
        order_id = _submit_limit_and_confirm(mock_delayed, executor_delayed)

        mod_result = executor_delayed.modify_limit_order(
            order_id=order_id, new_price=51000.0)

        assert mod_result.success is True
        assert mod_result.status == ModificationStatus.PENDING
        assert mod_result.order_id == order_id
        assert mod_result.rejection_reason is None

    def test_in_flight_operation_set_during_window(self, mock_delayed, executor_delayed):
        """After modify_limit_order schedule, target.execution_state.in_flight_operation == PENDING_MODIFY."""
        order_id = _submit_limit_and_confirm(mock_delayed, executor_delayed)

        executor_delayed.modify_limit_order(order_id=order_id, new_price=51000.0)

        target = next(p for p in executor_delayed._active_limit_orders
                      if p.pending_order_id == order_id)
        assert target.execution_state.in_flight_operation == PendingOperation.PENDING_MODIFY
        assert target.execution_state.pending_modification is not None
        assert target.execution_state.pending_modification.new_price == 51000.0

    def test_modification_applied_after_drain(self, mock_delayed, executor_delayed):
        """Drain inbox applies the EditResponse; entry_price reflects new value.

        Uses await_submit_confirmation (drain only, no on_tick) so DELAYED_FILL
        mode's Phase-2 polling doesn't fill the order — we want to inspect the
        modified state while it's still resting in _active_limit_orders.
        """
        order_id = _submit_limit_and_confirm(mock_delayed, executor_delayed)

        executor_delayed.modify_limit_order(
            order_id=order_id, new_price=51000.0,
            new_stop_loss=48000.0, new_take_profit=55000.0,
        )

        # Drain only — flush_outbox + drain_inbox, no on_tick / no Phase-2 polling
        mock_delayed.await_submit_confirmation(executor_delayed)

        target = next(p for p in executor_delayed._active_limit_orders
                      if p.pending_order_id == order_id)
        assert target.entry_price == 51000.0
        assert target.order_kwargs.get('stop_loss') == 48000.0
        assert target.order_kwargs.get('take_profit') == 55000.0

    def test_in_flight_clears_after_drain(self, mock_delayed, executor_delayed):
        """After drain, in_flight_operation back to NONE, pending_modification cleared."""
        order_id = _submit_limit_and_confirm(mock_delayed, executor_delayed)

        executor_delayed.modify_limit_order(order_id=order_id, new_price=51000.0)
        # Drain only — keep order in active list to inspect cleared state
        mock_delayed.await_submit_confirmation(executor_delayed)

        target = next(p for p in executor_delayed._active_limit_orders
                      if p.pending_order_id == order_id)
        assert target.execution_state.in_flight_operation == PendingOperation.NONE
        assert target.execution_state.pending_modification is None


class TestModifyLimitOrderBusy:
    """One in-flight operation per order — second call returns OPERATION_BUSY."""

    def test_second_modify_returns_busy(self, mock_delayed, executor_delayed):
        """Second modify while first is in-flight → OPERATION_BUSY."""
        order_id = _submit_limit_and_confirm(mock_delayed, executor_delayed)

        first = executor_delayed.modify_limit_order(order_id=order_id, new_price=51000.0)
        assert first.success is True

        second = executor_delayed.modify_limit_order(order_id=order_id, new_price=52000.0)
        assert second.success is False
        assert second.rejection_reason == ModificationRejectionReason.OPERATION_BUSY

    def test_modify_during_pending_cancel_returns_busy(self, mock_delayed, executor_delayed):
        """Modify on an order with PENDING_CANCEL in flight → OPERATION_BUSY."""
        order_id = _submit_limit_and_confirm(mock_delayed, executor_delayed)

        cancel_scheduled = executor_delayed.cancel_limit_order(order_id=order_id)
        assert cancel_scheduled is True

        mod_result = executor_delayed.modify_limit_order(
            order_id=order_id, new_price=51000.0)
        assert mod_result.success is False
        assert mod_result.rejection_reason == ModificationRejectionReason.OPERATION_BUSY


class TestModifyLimitOrderNotConfirmed:
    """Option A: modify on submit-in-flight order rejected with ORDER_NOT_CONFIRMED."""

    def test_modify_before_broker_ref_confirmed(self, mock_delayed, executor_delayed):
        """Order in _active_limit_orders with broker_ref=None → ORDER_NOT_CONFIRMED."""
        mock_delayed.feed_tick(executor_delayed, bid=49999.0, ask=50001.0)
        result = executor_delayed.open_order(OpenOrderRequest(
            symbol="BTCUSD", order_type=OrderType.LIMIT,
            direction=OrderDirection.LONG, lots=0.001, price=49000.0,
        ))
        order_id = result.order_id
        # Do NOT call await_submit_confirmation — broker_ref still None

        mod_result = executor_delayed.modify_limit_order(
            order_id=order_id, new_price=51000.0)
        assert mod_result.success is False
        assert mod_result.rejection_reason == ModificationRejectionReason.ORDER_NOT_CONFIRMED


class TestModifyLimitOrderNotFound:
    """Unknown order_id returns LIMIT_ORDER_NOT_FOUND."""

    def test_modify_nonexistent_order(self, mock_delayed, executor_delayed):
        """modify_limit_order on unknown order_id → LIMIT_ORDER_NOT_FOUND."""
        mock_delayed.feed_tick(executor_delayed, bid=49999.0, ask=50001.0)

        mod_result = executor_delayed.modify_limit_order(
            order_id="NONEXISTENT", new_price=51000.0)
        assert mod_result.success is False
        assert mod_result.rejection_reason == ModificationRejectionReason.LIMIT_ORDER_NOT_FOUND


class TestModifyStopOrderCapabilityGate:
    """modify_stop_order rejected when adapter doesn't declare STOP capability."""

    def test_modify_stop_order_rejected_for_kraken_profile(self, mock_delayed, executor_delayed):
        """Mock adapter declares stop_orders=False → ORDER_TYPE_NOT_SUPPORTED."""
        mod_result = executor_delayed.modify_stop_order(
            order_id="anything", new_stop_price=50000.0)
        assert mod_result.success is False
        assert mod_result.rejection_reason == ModificationRejectionReason.ORDER_TYPE_NOT_SUPPORTED


class TestModifyLimitOrderHasInFlight:
    """has_in_flight_operation reflects the modify state correctly."""

    def test_has_in_flight_operation_during_window(self, mock_delayed, executor_delayed):
        """During the in-flight window: has_in_flight_operation returns True."""
        order_id = _submit_limit_and_confirm(mock_delayed, executor_delayed)

        executor_delayed.modify_limit_order(order_id=order_id, new_price=51000.0)
        assert executor_delayed.has_in_flight_operation(order_id) is True

    def test_has_in_flight_operation_clears_after_drain(self, mock_delayed, executor_delayed):
        """After drain: has_in_flight_operation returns False because in_flight cleared.

        Uses await_submit_confirmation so the order STAYS in _active_limit_orders
        (no Phase-2 fill), confirming has_in_flight_operation returns False
        because the in_flight_operation flag was cleared, not because the order
        is gone.
        """
        order_id = _submit_limit_and_confirm(mock_delayed, executor_delayed)

        executor_delayed.modify_limit_order(order_id=order_id, new_price=51000.0)
        mock_delayed.await_submit_confirmation(executor_delayed)

        assert executor_delayed.has_in_flight_operation(order_id) is False
        # Also verify order is still in active list (cleared, not filled)
        assert any(p.pending_order_id == order_id
                   for p in executor_delayed._active_limit_orders)
