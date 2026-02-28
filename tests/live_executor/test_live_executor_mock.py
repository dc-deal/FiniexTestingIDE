# ============================================
# tests/live_executor/test_live_executor_mock.py
# ============================================
"""
LiveTradeExecutor + MockBrokerAdapter — Integration Tests

Tests the full execution pipeline: open_order() → broker response →
fill processing → order_history / portfolio update.

Each test creates a fresh executor via conftest fixtures.
MockOrderExecution provides tick feeding for pending order processing.
"""

from python.framework.testing.mock_adapter import MockExecutionMode
from python.framework.testing.mock_order_execution import MockOrderExecution
from python.framework.types.order_types import (
    OrderType,
    OrderDirection,
    OrderStatus,
    RejectionReason,
    OpenOrderRequest,
)


class TestInstantFill:
    """INSTANT_FILL mode: orders fill immediately on execute_order()."""

    def test_open_order_returns_executed(self, mock_instant, executor_instant):
        """open_order() returns EXECUTED status for instant fill."""
        mock_instant.feed_tick(executor_instant, bid=49999.0, ask=50001.0)

        result = executor_instant.open_order(OpenOrderRequest(
            symbol="BTCUSD", order_type=OrderType.MARKET, direction=OrderDirection.LONG, lots=0.001
        ))

        assert result.status == OrderStatus.EXECUTED
        assert result.executed_price is not None
        assert result.broker_order_id is not None

    def test_instant_fill_creates_position(self, mock_instant, executor_instant):
        """Instant fill creates an open position in portfolio."""
        mock_instant.feed_tick(executor_instant, bid=49999.0, ask=50001.0)

        executor_instant.open_order(OpenOrderRequest(
            symbol="BTCUSD", order_type=OrderType.MARKET, direction=OrderDirection.LONG, lots=0.001
        ))

        positions = executor_instant.get_open_positions()
        assert len(positions) == 1
        assert positions[0].symbol == "BTCUSD"
        assert positions[0].direction == OrderDirection.LONG

    def test_order_history_recorded(self, mock_instant, executor_instant):
        """Executed order appears in order history."""
        mock_instant.feed_tick(executor_instant, bid=49999.0, ask=50001.0)

        executor_instant.open_order(OpenOrderRequest(
            symbol="BTCUSD", order_type=OrderType.MARKET, direction=OrderDirection.LONG, lots=0.001
        ))

        history = executor_instant.get_order_history()
        assert len(history) >= 1
        assert history[0].status == OrderStatus.EXECUTED

    def test_no_pending_after_instant_fill(self, mock_instant, executor_instant):
        """Instant fill leaves no pending orders."""
        mock_instant.feed_tick(executor_instant, bid=49999.0, ask=50001.0)

        executor_instant.open_order(OpenOrderRequest(
            symbol="BTCUSD", order_type=OrderType.MARKET, direction=OrderDirection.LONG, lots=0.001
        ))

        assert not executor_instant.has_pending_orders()


class TestInstantFillClose:
    """INSTANT_FILL mode: close_position() fills immediately."""

    def test_close_position_returns_executed(self, mock_instant, executor_instant):
        """close_position() returns EXECUTED for instant fill."""
        mock_instant.feed_tick(executor_instant, bid=49999.0, ask=50001.0)

        executor_instant.open_order(OpenOrderRequest(
            symbol="BTCUSD", order_type=OrderType.MARKET, direction=OrderDirection.LONG, lots=0.001
        ))

        positions = executor_instant.get_open_positions()
        position_id = positions[0].position_id

        # Feed new tick at different price for close
        mock_instant.feed_tick(executor_instant, bid=50100.0, ask=50102.0)

        close_result = executor_instant.close_position(position_id)
        assert close_result.status == OrderStatus.EXECUTED

    def test_close_removes_from_open_positions(self, mock_instant, executor_instant):
        """Closed position no longer appears in open positions."""
        mock_instant.feed_tick(executor_instant, bid=49999.0, ask=50001.0)

        executor_instant.open_order(OpenOrderRequest(
            symbol="BTCUSD", order_type=OrderType.MARKET, direction=OrderDirection.LONG, lots=0.001
        ))

        positions = executor_instant.get_open_positions()
        position_id = positions[0].position_id

        mock_instant.feed_tick(executor_instant, bid=50100.0, ask=50102.0)
        executor_instant.close_position(position_id)

        assert len(executor_instant.get_open_positions()) == 0


class TestDelayedFill:
    """DELAYED_FILL mode: orders go PENDING, fill on next broker poll."""

    def test_open_order_returns_pending(self, mock_delayed, executor_delayed):
        """open_order() returns PENDING in delayed fill mode."""
        mock_delayed.feed_tick(executor_delayed, bid=49999.0, ask=50001.0)

        result = executor_delayed.open_order(OpenOrderRequest(
            symbol="BTCUSD", order_type=OrderType.MARKET, direction=OrderDirection.LONG, lots=0.001
        ))

        assert result.status == OrderStatus.PENDING
        assert result.broker_order_id is not None

    def test_pending_order_tracked(self, mock_delayed, executor_delayed):
        """Delayed order is tracked as pending."""
        mock_delayed.feed_tick(executor_delayed, bid=49999.0, ask=50001.0)

        executor_delayed.open_order(OpenOrderRequest(
            symbol="BTCUSD", order_type=OrderType.MARKET, direction=OrderDirection.LONG, lots=0.001
        ))

        assert executor_delayed.has_pending_orders()

    def test_delayed_fill_on_next_tick(self, mock_delayed, executor_delayed):
        """Pending order fills when next tick triggers _process_pending_orders()."""
        mock_delayed.feed_tick(executor_delayed, bid=49999.0, ask=50001.0)

        executor_delayed.open_order(OpenOrderRequest(
            symbol="BTCUSD", order_type=OrderType.MARKET, direction=OrderDirection.LONG, lots=0.001
        ))

        # Next tick triggers on_tick() → _process_pending_orders() → broker poll → fill
        mock_delayed.feed_tick(executor_delayed, bid=50050.0, ask=50052.0)

        positions = executor_delayed.get_open_positions()
        assert len(positions) == 1
        assert not executor_delayed.has_pending_orders()


class TestRejection:
    """REJECT_ALL mode: all orders are rejected by broker."""

    def test_rejected_order_status(self, mock_reject, executor_reject):
        """open_order() returns REJECTED status."""
        mock_reject.feed_tick(executor_reject, bid=49999.0, ask=50001.0)

        result = executor_reject.open_order(OpenOrderRequest(
            symbol="BTCUSD", order_type=OrderType.MARKET, direction=OrderDirection.LONG, lots=0.001
        ))

        assert result.status == OrderStatus.REJECTED

    def test_rejected_order_in_history(self, mock_reject, executor_reject):
        """Rejected order recorded in order history."""
        mock_reject.feed_tick(executor_reject, bid=49999.0, ask=50001.0)

        executor_reject.open_order(OpenOrderRequest(
            symbol="BTCUSD", order_type=OrderType.MARKET, direction=OrderDirection.LONG, lots=0.001
        ))

        history = executor_reject.get_order_history()
        rejected = [h for h in history if h.status == OrderStatus.REJECTED]
        assert len(rejected) >= 1

    def test_no_position_created_on_rejection(self, mock_reject, executor_reject):
        """Rejected order does not create any position."""
        mock_reject.feed_tick(executor_reject, bid=49999.0, ask=50001.0)

        executor_reject.open_order(OpenOrderRequest(
            symbol="BTCUSD", order_type=OrderType.MARKET, direction=OrderDirection.LONG, lots=0.001
        ))

        assert len(executor_reject.get_open_positions()) == 0


class TestFeatureGating:
    """Feature gating: only MARKET and LIMIT orders allowed."""

    def test_stop_order_rejected(self, mock_instant, executor_instant):
        """STOP order type is rejected with ORDER_TYPE_NOT_SUPPORTED."""
        mock_instant.feed_tick(executor_instant, bid=49999.0, ask=50001.0)

        result = executor_instant.open_order(OpenOrderRequest(
            symbol="BTCUSD", order_type=OrderType.STOP, direction=OrderDirection.LONG, lots=0.001
        ))

        assert result.status == OrderStatus.REJECTED
        assert result.rejection_reason == RejectionReason.ORDER_TYPE_NOT_SUPPORTED

    def test_stop_limit_order_rejected(self, mock_instant, executor_instant):
        """STOP_LIMIT order type is rejected."""
        mock_instant.feed_tick(executor_instant, bid=49999.0, ask=50001.0)

        result = executor_instant.open_order(OpenOrderRequest(
            symbol="BTCUSD", order_type=OrderType.STOP_LIMIT, direction=OrderDirection.LONG, lots=0.001
        ))

        assert result.status == OrderStatus.REJECTED


class TestValidation:
    """Order validation against broker limits."""

    def test_invalid_symbol_rejected(self, mock_instant, executor_instant):
        """Order for unknown symbol is rejected."""
        mock_instant.feed_tick(executor_instant, bid=49999.0, ask=50001.0)

        result = executor_instant.open_order(OpenOrderRequest(
            symbol="INVALID_SYMBOL", order_type=OrderType.MARKET, direction=OrderDirection.LONG, lots=0.001
        ))

        assert result.status == OrderStatus.REJECTED

    def test_close_nonexistent_position_rejected(self, mock_instant, executor_instant):
        """Closing non-existent position returns REJECTED."""
        result = executor_instant.close_position("NONEXISTENT-POS")

        assert result.status == OrderStatus.REJECTED


class TestExecutionStats:
    """Execution statistics consistency."""

    def test_stats_after_instant_fill(self, mock_instant, executor_instant):
        """Execution stats reflect completed order."""
        mock_instant.feed_tick(executor_instant, bid=49999.0, ask=50001.0)

        executor_instant.open_order(OpenOrderRequest(
            symbol="BTCUSD", order_type=OrderType.MARKET, direction=OrderDirection.LONG, lots=0.001
        ))

        stats = executor_instant.get_execution_stats()
        assert stats.orders_sent == 1
        assert stats.orders_executed >= 1

    def test_stats_after_rejection(self, mock_reject, executor_reject):
        """Execution stats count rejections."""
        mock_reject.feed_tick(executor_reject, bid=49999.0, ask=50001.0)

        executor_reject.open_order(OpenOrderRequest(
            symbol="BTCUSD", order_type=OrderType.MARKET, direction=OrderDirection.LONG, lots=0.001
        ))

        stats = executor_reject.get_execution_stats()
        assert stats.orders_sent == 1
        assert stats.orders_rejected >= 1


class TestNotLiveCapable:
    """Adapter that is not live-capable cannot create LiveTradeExecutor."""

    def test_non_live_adapter_raises(self):
        """LiveTradeExecutor raises ValueError for non-live adapter."""
        from python.framework.trading_env.broker_config import BrokerConfig
        from python.framework.testing.mock_adapter import MockBrokerAdapter
        from python.framework.types.broker_types import BrokerType
        from python.framework.logging.global_logger import GlobalLogger

        adapter = MockBrokerAdapter(mode=MockExecutionMode.INSTANT_FILL)
        # Monkey-patch to simulate non-live adapter
        adapter.is_live_capable = lambda: False

        broker_config = BrokerConfig(BrokerType.KRAKEN_SPOT, adapter)
        logger = GlobalLogger(name="TestNonLive")

        import pytest
        with pytest.raises(ValueError, match="not live-capable"):
            from python.framework.trading_env.live.live_trade_executor import LiveTradeExecutor
            LiveTradeExecutor(
                broker_config=broker_config,
                initial_balance=10000.0,
                account_currency="USD",
                logger=logger,
            )
