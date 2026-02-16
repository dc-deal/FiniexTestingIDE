# ============================================
# tests/live_executor/test_live_order_tracker.py
# ============================================
"""
LiveOrderTracker — Isolated Unit Tests

Tests the time-based pending order manager independently from
LiveTradeExecutor and MockBrokerAdapter. Validates:
- Order submission and storage
- Broker reference index (O(1) lookup)
- Fill and rejection marking
- Timeout detection
- Close order tracking
- Cleanup behavior
"""

from datetime import datetime, timedelta, timezone

from python.framework.trading_env.live_order_tracker import LiveOrderTracker
from python.framework.types.latency_simulator_types import PendingOrderAction
from python.framework.types.live_execution_types import TimeoutConfig
from python.framework.types.order_types import OrderDirection


class TestSubmitAndQuery:
    """Submit orders and verify storage/query methods."""

    def test_submit_order_tracked(self, order_tracker):
        """Submitted order appears in pending orders."""
        order_tracker.submit_order(
            order_id="ORD-001",
            symbol="BTCUSD",
            direction=OrderDirection.LONG,
            lots=0.001,
            broker_ref="MOCK-000001",
        )

        assert order_tracker.has_pending_orders()
        assert order_tracker.get_pending_count() == 1

    def test_submit_order_returns_order_id(self, order_tracker):
        """submit_order() returns the order_id for chaining."""
        result = order_tracker.submit_order(
            order_id="ORD-002",
            symbol="BTCUSD",
            direction=OrderDirection.LONG,
            lots=0.001,
            broker_ref="MOCK-000002",
        )
        assert result == "ORD-002"

    def test_broker_ref_lookup(self, order_tracker):
        """Broker reference index provides O(1) lookup."""
        order_tracker.submit_order(
            order_id="ORD-003",
            symbol="BTCUSD",
            direction=OrderDirection.SHORT,
            lots=0.01,
            broker_ref="MOCK-000003",
        )

        pending = order_tracker.get_by_broker_ref("MOCK-000003")
        assert pending is not None
        assert pending.pending_order_id == "ORD-003"
        assert pending.symbol == "BTCUSD"
        assert pending.direction == OrderDirection.SHORT

    def test_unknown_broker_ref_returns_none(self, order_tracker):
        """Lookup with unknown broker_ref returns None."""
        result = order_tracker.get_by_broker_ref("NONEXISTENT")
        assert result is None

    def test_pending_order_has_live_fields(self, order_tracker):
        """Submitted order has submitted_at, broker_ref, timeout_at set."""
        order_tracker.submit_order(
            order_id="ORD-004",
            symbol="BTCUSD",
            direction=OrderDirection.LONG,
            lots=0.001,
            broker_ref="MOCK-000004",
        )

        pending = order_tracker.get_by_broker_ref("MOCK-000004")
        assert pending.submitted_at is not None
        assert pending.broker_ref == "MOCK-000004"
        assert pending.timeout_at is not None
        assert pending.timeout_at > pending.submitted_at

    def test_pending_order_action_is_open(self, order_tracker):
        """Submitted open order has action=OPEN."""
        order_tracker.submit_order(
            order_id="ORD-005",
            symbol="BTCUSD",
            direction=OrderDirection.LONG,
            lots=0.001,
            broker_ref="MOCK-000005",
        )

        pending = order_tracker.get_by_broker_ref("MOCK-000005")
        assert pending.order_action == PendingOrderAction.OPEN


class TestMarkFilled:
    """Mark orders as filled and verify removal from pending."""

    def test_mark_filled_returns_pending_order(self, order_tracker):
        """mark_filled() returns the PendingOrder for fill processing."""
        order_tracker.submit_order(
            order_id="ORD-010",
            symbol="BTCUSD",
            direction=OrderDirection.LONG,
            lots=0.001,
            broker_ref="MOCK-000010",
        )

        filled = order_tracker.mark_filled(
            broker_ref="MOCK-000010",
            fill_price=50000.0,
            filled_lots=0.001,
        )

        assert filled is not None
        assert filled.pending_order_id == "ORD-010"
        assert filled.symbol == "BTCUSD"

    def test_mark_filled_removes_from_pending(self, order_tracker):
        """Filled order is no longer in pending storage."""
        order_tracker.submit_order(
            order_id="ORD-011",
            symbol="BTCUSD",
            direction=OrderDirection.LONG,
            lots=0.001,
            broker_ref="MOCK-000011",
        )

        order_tracker.mark_filled(
            broker_ref="MOCK-000011",
            fill_price=50000.0,
            filled_lots=0.001,
        )

        assert not order_tracker.has_pending_orders()
        assert order_tracker.get_by_broker_ref("MOCK-000011") is None

    def test_mark_filled_unknown_ref_returns_none(self, order_tracker):
        """mark_filled() with unknown broker_ref returns None."""
        result = order_tracker.mark_filled(
            broker_ref="NONEXISTENT",
            fill_price=50000.0,
            filled_lots=0.001,
        )
        assert result is None


class TestMarkRejected:
    """Mark orders as rejected and verify removal from pending."""

    def test_mark_rejected_returns_pending_order(self, order_tracker):
        """mark_rejected() returns the PendingOrder for rejection recording."""
        order_tracker.submit_order(
            order_id="ORD-020",
            symbol="BTCUSD",
            direction=OrderDirection.LONG,
            lots=0.001,
            broker_ref="MOCK-000020",
        )

        rejected = order_tracker.mark_rejected(
            broker_ref="MOCK-000020",
            reason="insufficient_margin",
        )

        assert rejected is not None
        assert rejected.pending_order_id == "ORD-020"

    def test_mark_rejected_removes_from_pending(self, order_tracker):
        """Rejected order is no longer in pending storage."""
        order_tracker.submit_order(
            order_id="ORD-021",
            symbol="BTCUSD",
            direction=OrderDirection.LONG,
            lots=0.001,
            broker_ref="MOCK-000021",
        )

        order_tracker.mark_rejected(
            broker_ref="MOCK-000021",
            reason="broker_error",
        )

        assert not order_tracker.has_pending_orders()

    def test_mark_rejected_unknown_ref_returns_none(self, order_tracker):
        """mark_rejected() with unknown broker_ref returns None."""
        result = order_tracker.mark_rejected(
            broker_ref="NONEXISTENT",
            reason="unknown",
        )
        assert result is None


class TestTimeoutDetection:
    """Timeout detection for unresponsive orders."""

    def test_no_timeouts_within_window(self, order_tracker):
        """Orders within timeout window are not flagged."""
        order_tracker.submit_order(
            order_id="ORD-030",
            symbol="BTCUSD",
            direction=OrderDirection.LONG,
            lots=0.001,
            broker_ref="MOCK-000030",
        )

        timed_out = order_tracker.check_timeouts()
        assert len(timed_out) == 0

    def test_timeout_detected_after_expiry(self, logger):
        """Orders past timeout_at are detected by check_timeouts()."""
        # Use 0-second timeout so order is immediately expired
        fast_timeout = TimeoutConfig(order_timeout_seconds=0.0)
        tracker = LiveOrderTracker(logger=logger, timeout_config=fast_timeout)

        tracker.submit_order(
            order_id="ORD-031",
            symbol="BTCUSD",
            direction=OrderDirection.LONG,
            lots=0.001,
            broker_ref="MOCK-000031",
        )

        timed_out = tracker.check_timeouts()
        assert len(timed_out) == 1
        assert timed_out[0].pending_order_id == "ORD-031"

    def test_timeout_does_not_remove_order(self, logger):
        """check_timeouts() returns but does NOT remove expired orders."""
        fast_timeout = TimeoutConfig(order_timeout_seconds=0.0)
        tracker = LiveOrderTracker(logger=logger, timeout_config=fast_timeout)

        tracker.submit_order(
            order_id="ORD-032",
            symbol="BTCUSD",
            direction=OrderDirection.LONG,
            lots=0.001,
            broker_ref="MOCK-000032",
        )

        tracker.check_timeouts()
        # Order still in pending — caller decides how to handle
        assert tracker.has_pending_orders()


class TestCloseOrderTracking:
    """Close order submission and pending close detection."""

    def test_submit_close_order_tracked(self, order_tracker):
        """Close order appears in pending orders."""
        order_tracker.submit_close_order(
            position_id="POS-001",
            broker_ref="MOCK-CLOSE-001",
            close_lots=0.001,
        )

        assert order_tracker.has_pending_orders()
        assert order_tracker.is_pending_close("POS-001")

    def test_close_order_action_is_close(self, order_tracker):
        """Close order has action=CLOSE."""
        order_tracker.submit_close_order(
            position_id="POS-002",
            broker_ref="MOCK-CLOSE-002",
            close_lots=0.001,
        )

        pending = order_tracker.get_by_broker_ref("MOCK-CLOSE-002")
        assert pending.order_action == PendingOrderAction.CLOSE

    def test_is_pending_close_false_for_open(self, order_tracker):
        """is_pending_close() returns False for open orders."""
        order_tracker.submit_order(
            order_id="ORD-040",
            symbol="BTCUSD",
            direction=OrderDirection.LONG,
            lots=0.001,
            broker_ref="MOCK-000040",
        )

        assert not order_tracker.is_pending_close("ORD-040")


class TestClearPending:
    """Cleanup behavior for clear_pending()."""

    def test_clear_removes_all_orders(self, order_tracker):
        """clear_pending() removes all pending orders."""
        order_tracker.submit_order(
            order_id="ORD-050",
            symbol="BTCUSD",
            direction=OrderDirection.LONG,
            lots=0.001,
            broker_ref="MOCK-000050",
        )
        order_tracker.submit_order(
            order_id="ORD-051",
            symbol="BTCUSD",
            direction=OrderDirection.SHORT,
            lots=0.002,
            broker_ref="MOCK-000051",
        )

        order_tracker.clear_pending()

        assert not order_tracker.has_pending_orders()
        assert order_tracker.get_pending_count() == 0

    def test_clear_also_clears_broker_ref_index(self, order_tracker):
        """clear_pending() clears the broker_ref index."""
        order_tracker.submit_order(
            order_id="ORD-052",
            symbol="BTCUSD",
            direction=OrderDirection.LONG,
            lots=0.001,
            broker_ref="MOCK-000052",
        )

        order_tracker.clear_pending()

        assert order_tracker.get_by_broker_ref("MOCK-000052") is None
