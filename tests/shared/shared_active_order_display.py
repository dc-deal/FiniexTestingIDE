"""
FiniexTestingIDE - Shared Active Order Display Tests
Reusable test classes for active limit/stop order reporting via pending_stats.

Validates that orders placed at unreachable prices remain in
active_limit_orders / active_stop_orders at scenario end, and that the
opposing list stays empty.

Used by: active_order_display
Import these classes into suite-specific test files.
"""

from python.framework.types.pending_order_stats_types import PendingOrderStats
from python.framework.types.order_types import OrderDirection, OrderType


class TestActiveLimitOrdersReported:
    """
    Tests for active limit order reporting.

    Scenario: LONG LIMIT at price 0.5000 (far below market) — never fills.
    Expects active_limit_orders to contain exactly 1 entry at scenario end.
    """

    def test_active_limit_orders_populated(self, pending_stats_limit: PendingOrderStats):
        """active_limit_orders must contain exactly 1 entry."""
        assert len(pending_stats_limit.active_limit_orders) == 1, (
            f"Expected 1 active limit order, "
            f"got {len(pending_stats_limit.active_limit_orders)}"
        )

    def test_active_limit_order_direction(self, pending_stats_limit: PendingOrderStats):
        """Active limit order direction must be LONG."""
        order = pending_stats_limit.active_limit_orders[0]
        assert order.direction == OrderDirection.LONG, (
            f"Expected LONG, got {order.direction}"
        )

    def test_active_limit_order_type(self, pending_stats_limit: PendingOrderStats):
        """Active limit order type must be LIMIT."""
        order = pending_stats_limit.active_limit_orders[0]
        assert order.order_type == OrderType.LIMIT, (
            f"Expected LIMIT, got {order.order_type}"
        )

    def test_active_limit_order_entry_price(self, pending_stats_limit: PendingOrderStats):
        """Active limit order entry_price must match configured price (0.5000)."""
        order = pending_stats_limit.active_limit_orders[0]
        assert order.entry_price == 0.5000, (
            f"Expected entry_price=0.5000, got {order.entry_price}"
        )

    def test_active_stop_orders_empty(self, pending_stats_limit: PendingOrderStats):
        """active_stop_orders must be empty in the limit scenario."""
        assert len(pending_stats_limit.active_stop_orders) == 0, (
            f"Expected 0 active stop orders, "
            f"got {len(pending_stats_limit.active_stop_orders)}"
        )


class TestActiveStopOrdersReported:
    """
    Tests for active stop order reporting.

    Scenario: LONG STOP at stop_price 5.0000 (far above market) — never triggers.
    Expects active_stop_orders to contain exactly 1 entry at scenario end.
    """

    def test_active_stop_orders_populated(self, pending_stats_stop: PendingOrderStats):
        """active_stop_orders must contain exactly 1 entry."""
        assert len(pending_stats_stop.active_stop_orders) == 1, (
            f"Expected 1 active stop order, "
            f"got {len(pending_stats_stop.active_stop_orders)}"
        )

    def test_active_stop_order_direction(self, pending_stats_stop: PendingOrderStats):
        """Active stop order direction must be LONG."""
        order = pending_stats_stop.active_stop_orders[0]
        assert order.direction == OrderDirection.LONG, (
            f"Expected LONG, got {order.direction}"
        )

    def test_active_stop_order_type(self, pending_stats_stop: PendingOrderStats):
        """Active stop order type must be STOP."""
        order = pending_stats_stop.active_stop_orders[0]
        assert order.order_type == OrderType.STOP, (
            f"Expected STOP, got {order.order_type}"
        )

    def test_active_stop_order_entry_price(self, pending_stats_stop: PendingOrderStats):
        """Active stop order entry_price must match configured stop_price (5.0000)."""
        order = pending_stats_stop.active_stop_orders[0]
        assert order.entry_price == 5.0000, (
            f"Expected entry_price=5.0000, got {order.entry_price}"
        )

    def test_active_limit_orders_empty(self, pending_stats_stop: PendingOrderStats):
        """active_limit_orders must be empty in the stop scenario."""
        assert len(pending_stats_stop.active_limit_orders) == 0, (
            f"Expected 0 active limit orders, "
            f"got {len(pending_stats_stop.active_limit_orders)}"
        )
