# ============================================
# tests/live_executor/test_live_executor_modify.py
# ============================================
"""
LiveTradeExecutor — Limit Order Modification Tests

Tests modify_limit_order() via broker adapter:
- Successful modification of pending limit orders
- Non-existent order rejection
- Broker rejection handling
- Adapter exception handling
- LiveOrderTracker.get_broker_ref() reverse lookup

Uses DELAYED_FILL mode so orders stay in pending (broker_ref tracked).
"""

from python.framework.testing.mock_adapter import MockBrokerAdapter, MockExecutionMode
from python.framework.testing.mock_order_execution import MockOrderExecution
from python.framework.trading_env.live_trade_executor import LiveTradeExecutor
from python.framework.types.order_types import (
    OrderType,
    OrderDirection,
    OrderStatus,
    ModificationRejectionReason,
    OpenOrderRequest,
)


class TestModifyLimitOrderSuccess:
    """Successful modification of pending limit orders via broker."""

    def test_modify_pending_order_price(self, mock_delayed, executor_delayed):
        """modify_limit_order() succeeds for order tracked in LiveOrderTracker."""
        mock_delayed.feed_tick(executor_delayed, bid=49999.0, ask=50001.0)

        # Submit order — stays PENDING in delayed mode
        result = executor_delayed.open_order(OpenOrderRequest(
            symbol="BTCUSD", order_type=OrderType.MARKET,
            direction=OrderDirection.LONG, lots=0.001
        ))
        assert result.status == OrderStatus.PENDING
        order_id = result.order_id

        # Modify the pending order's price
        mod_result = executor_delayed.modify_limit_order(
            order_id=order_id, new_price=51000.0)

        assert mod_result.success is True
        assert mod_result.rejection_reason is None

    def test_modify_pending_order_sl_tp(self, mock_delayed, executor_delayed):
        """modify_limit_order() can modify SL and TP on pending order."""
        mock_delayed.feed_tick(executor_delayed, bid=49999.0, ask=50001.0)

        result = executor_delayed.open_order(OpenOrderRequest(
            symbol="BTCUSD", order_type=OrderType.MARKET,
            direction=OrderDirection.LONG, lots=0.001
        ))
        order_id = result.order_id

        mod_result = executor_delayed.modify_limit_order(
            order_id=order_id,
            new_stop_loss=48000.0,
            new_take_profit=55000.0)

        assert mod_result.success is True

    def test_modify_with_unset_keeps_current(self, mock_delayed, executor_delayed):
        """UNSET parameters are translated to None (no change) for adapter."""
        mock_delayed.feed_tick(executor_delayed, bid=49999.0, ask=50001.0)

        result = executor_delayed.open_order(OpenOrderRequest(
            symbol="BTCUSD", order_type=OrderType.MARKET,
            direction=OrderDirection.LONG, lots=0.001
        ))
        order_id = result.order_id

        # Only modify price, leave SL/TP as UNSET (default)
        mod_result = executor_delayed.modify_limit_order(
            order_id=order_id, new_price=52000.0)

        assert mod_result.success is True


class TestModifyLimitOrderNotFound:
    """Modification fails for non-existent orders."""

    def test_modify_nonexistent_order(self, mock_delayed, executor_delayed):
        """modify_limit_order() returns LIMIT_ORDER_NOT_FOUND for unknown order_id."""
        mock_delayed.feed_tick(executor_delayed, bid=49999.0, ask=50001.0)

        mod_result = executor_delayed.modify_limit_order(
            order_id="NONEXISTENT-ORDER", new_price=51000.0)

        assert mod_result.success is False
        assert mod_result.rejection_reason == ModificationRejectionReason.LIMIT_ORDER_NOT_FOUND

    def test_modify_after_fill_returns_not_found(self, mock_delayed, executor_delayed):
        """modify_limit_order() fails after order has been filled (removed from tracker)."""
        mock_delayed.feed_tick(executor_delayed, bid=49999.0, ask=50001.0)

        result = executor_delayed.open_order(OpenOrderRequest(
            symbol="BTCUSD", order_type=OrderType.MARKET,
            direction=OrderDirection.LONG, lots=0.001
        ))
        order_id = result.order_id

        # Next tick fills the order (delayed mode fills on first status check)
        mock_delayed.feed_tick(executor_delayed, bid=50050.0, ask=50052.0)
        assert not executor_delayed.has_pending_orders()

        # Now try to modify — order is gone from tracker
        mod_result = executor_delayed.modify_limit_order(
            order_id=order_id, new_price=51000.0)

        assert mod_result.success is False
        assert mod_result.rejection_reason == ModificationRejectionReason.LIMIT_ORDER_NOT_FOUND


class TestModifyLimitOrderBrokerRejection:
    """Broker rejects the modification."""

    def test_broker_rejects_modify(self):
        """modify_limit_order() returns failure when broker rejects."""
        # Start with delayed mode to get a pending order
        mock = MockOrderExecution(mode=MockExecutionMode.DELAYED_FILL)
        executor = mock.create_executor()
        mock.feed_tick(executor, bid=49999.0, ask=50001.0)

        result = executor.open_order(OpenOrderRequest(
            symbol="BTCUSD", order_type=OrderType.MARKET,
            direction=OrderDirection.LONG, lots=0.001
        ))
        order_id = result.order_id

        # Switch adapter to reject_all mode before modify
        executor.broker.adapter.set_mode(MockExecutionMode.REJECT_ALL)

        mod_result = executor.modify_limit_order(
            order_id=order_id, new_price=51000.0)

        assert mod_result.success is False
        assert mod_result.rejection_reason == ModificationRejectionReason.INVALID_PRICE


class TestModifyLimitOrderAdapterException:
    """Adapter raises exception during modify_order()."""

    def test_adapter_exception_handled(self, mock_delayed, executor_delayed):
        """modify_limit_order() handles adapter exceptions gracefully."""
        mock_delayed.feed_tick(executor_delayed, bid=49999.0, ask=50001.0)

        result = executor_delayed.open_order(OpenOrderRequest(
            symbol="BTCUSD", order_type=OrderType.MARKET,
            direction=OrderDirection.LONG, lots=0.001
        ))
        order_id = result.order_id

        # Monkey-patch adapter to raise on modify_order
        def raise_on_modify(*args, **kwargs):
            raise ConnectionError("Broker connection lost")

        executor_delayed.broker.adapter.modify_order = raise_on_modify

        mod_result = executor_delayed.modify_limit_order(
            order_id=order_id, new_price=51000.0)

        assert mod_result.success is False
        assert mod_result.rejection_reason == ModificationRejectionReason.INVALID_PRICE


class TestGetBrokerRefReverseLookup:
    """LiveOrderTracker.get_broker_ref() reverse lookup tests."""

    def test_get_broker_ref_returns_ref(self, order_tracker):
        """get_broker_ref() returns broker_ref for known order_id."""
        order_tracker.submit_order(
            order_id="ORD-100",
            symbol="BTCUSD",
            direction=OrderDirection.LONG,
            lots=0.001,
            broker_ref="MOCK-000100",
        )

        broker_ref = order_tracker.get_broker_ref("ORD-100")
        assert broker_ref == "MOCK-000100"

    def test_get_broker_ref_unknown_returns_none(self, order_tracker):
        """get_broker_ref() returns None for unknown order_id."""
        broker_ref = order_tracker.get_broker_ref("NONEXISTENT")
        assert broker_ref is None

    def test_get_broker_ref_after_fill_returns_none(self, order_tracker):
        """get_broker_ref() returns None after order is filled (removed from index)."""
        order_tracker.submit_order(
            order_id="ORD-101",
            symbol="BTCUSD",
            direction=OrderDirection.LONG,
            lots=0.001,
            broker_ref="MOCK-000101",
        )

        order_tracker.mark_filled(
            broker_ref="MOCK-000101",
            fill_price=50000.0,
            filled_lots=0.001,
        )

        broker_ref = order_tracker.get_broker_ref("ORD-101")
        assert broker_ref is None

    def test_get_broker_ref_multiple_orders(self, order_tracker):
        """get_broker_ref() returns correct ref when multiple orders tracked."""
        order_tracker.submit_order(
            order_id="ORD-102",
            symbol="BTCUSD",
            direction=OrderDirection.LONG,
            lots=0.001,
            broker_ref="MOCK-000102",
        )
        order_tracker.submit_order(
            order_id="ORD-103",
            symbol="BTCUSD",
            direction=OrderDirection.SHORT,
            lots=0.002,
            broker_ref="MOCK-000103",
        )

        assert order_tracker.get_broker_ref("ORD-102") == "MOCK-000102"
        assert order_tracker.get_broker_ref("ORD-103") == "MOCK-000103"
