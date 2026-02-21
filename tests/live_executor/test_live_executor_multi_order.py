# ============================================
# tests/live_executor/test_live_executor_multi_order.py
# ============================================
"""
LiveTradeExecutor — Multi-Order Scenario Tests

Tests that involve multiple orders, open+close cycles, and
portfolio-level consistency checks. These scenarios validate
that the full pipeline handles realistic order sequences.
"""

from python.framework.testing.mock_adapter import MockExecutionMode
from python.framework.testing.mock_order_execution import MockOrderExecution
from python.framework.types.order_types import (
    OrderType,
    OrderDirection,
    OrderStatus,
    OpenOrderRequest,
)


class TestMultipleOrdersTracked:
    """Multiple orders submitted and tracked independently."""

    def test_two_orders_both_fill(self):
        """Two instant-fill orders both create positions."""
        mock = MockOrderExecution(mode=MockExecutionMode.INSTANT_FILL)
        executor = mock.create_executor()

        mock.feed_tick(executor, bid=49999.0, ask=50001.0)

        executor.open_order(OpenOrderRequest(symbol="BTCUSD", order_type=OrderType.MARKET, direction=OrderDirection.LONG, lots=0.001))
        executor.open_order(OpenOrderRequest(symbol="BTCUSD", order_type=OrderType.MARKET, direction=OrderDirection.SHORT, lots=0.001))

        positions = executor.get_open_positions()
        assert len(positions) == 2

    def test_multiple_delayed_fills(self):
        """Multiple delayed orders all fill after tick."""
        mock = MockOrderExecution(mode=MockExecutionMode.DELAYED_FILL)
        executor = mock.create_executor()

        mock.feed_tick(executor, bid=49999.0, ask=50001.0)

        executor.open_order(OpenOrderRequest(symbol="BTCUSD", order_type=OrderType.MARKET, direction=OrderDirection.LONG, lots=0.001))
        executor.open_order(OpenOrderRequest(symbol="BTCUSD", order_type=OrderType.MARKET, direction=OrderDirection.SHORT, lots=0.001))

        assert executor.has_pending_orders()

        # Tick triggers pending order processing
        mock.feed_tick(executor, bid=50050.0, ask=50052.0)

        positions = executor.get_open_positions()
        assert len(positions) == 2
        assert not executor.has_pending_orders()

    def test_order_history_tracks_all(self):
        """Order history contains entries for all submitted orders."""
        mock = MockOrderExecution(mode=MockExecutionMode.INSTANT_FILL)
        executor = mock.create_executor()

        mock.feed_tick(executor, bid=49999.0, ask=50001.0)

        executor.open_order(OpenOrderRequest(symbol="BTCUSD", order_type=OrderType.MARKET, direction=OrderDirection.LONG, lots=0.001))
        executor.open_order(OpenOrderRequest(symbol="BTCUSD", order_type=OrderType.MARKET, direction=OrderDirection.SHORT, lots=0.001))
        executor.open_order(OpenOrderRequest(symbol="BTCUSD", order_type=OrderType.MARKET, direction=OrderDirection.LONG, lots=0.002))

        history = executor.get_order_history()
        assert len(history) >= 3


class TestOpenCloseCycle:
    """Full open → close cycle with portfolio verification."""

    def test_open_close_cycle_completes(self):
        """Open and close order completes full cycle."""
        mock = MockOrderExecution(mode=MockExecutionMode.INSTANT_FILL)
        executor = mock.create_executor()

        mock.feed_tick(executor, bid=49999.0, ask=50001.0)

        # Open
        open_result = executor.open_order(OpenOrderRequest(
            symbol="BTCUSD", order_type=OrderType.MARKET, direction=OrderDirection.LONG, lots=0.001
        ))
        assert open_result.status == OrderStatus.EXECUTED

        positions = executor.get_open_positions()
        assert len(positions) == 1
        position_id = positions[0].position_id

        # Close at different price
        mock.feed_tick(executor, bid=50100.0, ask=50102.0)
        close_result = executor.close_position(position_id)
        assert close_result.status == OrderStatus.EXECUTED

        # No open positions remain
        assert len(executor.get_open_positions()) == 0

    def test_trade_history_after_close(self):
        """Closed trade appears in trade history with P&L."""
        mock = MockOrderExecution(mode=MockExecutionMode.INSTANT_FILL)
        executor = mock.create_executor()

        mock.feed_tick(executor, bid=49999.0, ask=50001.0)
        executor.open_order(OpenOrderRequest(
            symbol="BTCUSD", order_type=OrderType.MARKET, direction=OrderDirection.LONG, lots=0.001
        ))

        positions = executor.get_open_positions()
        position_id = positions[0].position_id

        mock.feed_tick(executor, bid=50100.0, ask=50102.0)
        executor.close_position(position_id)

        trade_history = executor.get_trade_history()
        assert len(trade_history) >= 1


class TestCloseAllRemaining:
    """close_all_remaining_orders() cleanup."""

    def test_close_all_closes_open_positions(self):
        """close_all_remaining_orders() closes all positions."""
        mock = MockOrderExecution(mode=MockExecutionMode.INSTANT_FILL)
        executor = mock.create_executor()

        mock.feed_tick(executor, bid=49999.0, ask=50001.0)

        executor.open_order(OpenOrderRequest(symbol="BTCUSD", order_type=OrderType.MARKET, direction=OrderDirection.LONG, lots=0.001))
        executor.open_order(OpenOrderRequest(symbol="BTCUSD", order_type=OrderType.MARKET, direction=OrderDirection.SHORT, lots=0.002))

        assert len(executor.get_open_positions()) == 2

        mock.feed_tick(executor, bid=50050.0, ask=50052.0)
        executor.close_all_remaining_orders()

        assert len(executor.get_open_positions()) == 0

    def test_close_all_on_empty_portfolio(self):
        """close_all_remaining_orders() handles empty portfolio gracefully."""
        mock = MockOrderExecution(mode=MockExecutionMode.INSTANT_FILL)
        executor = mock.create_executor()

        mock.feed_tick(executor, bid=49999.0, ask=50001.0)

        # No positions open — should not raise
        executor.close_all_remaining_orders()
        assert len(executor.get_open_positions()) == 0


class TestStatsConsistency:
    """Execution stats stay consistent across multiple operations."""

    def test_sent_equals_executed_plus_rejected(self):
        """orders_sent == orders_executed + orders_rejected (all modes)."""
        mock = MockOrderExecution(mode=MockExecutionMode.INSTANT_FILL)
        executor = mock.create_executor()

        mock.feed_tick(executor, bid=49999.0, ask=50001.0)

        # 2 successful + 1 rejected (STOP)
        executor.open_order(OpenOrderRequest(symbol="BTCUSD", order_type=OrderType.MARKET, direction=OrderDirection.LONG, lots=0.001))
        executor.open_order(OpenOrderRequest(symbol="BTCUSD", order_type=OrderType.MARKET, direction=OrderDirection.SHORT, lots=0.001))
        executor.open_order(OpenOrderRequest(symbol="BTCUSD", order_type=OrderType.STOP, direction=OrderDirection.LONG, lots=0.001))

        stats = executor.get_execution_stats()
        assert stats.orders_sent == stats.orders_executed + stats.orders_rejected
