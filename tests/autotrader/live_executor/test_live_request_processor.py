"""
LiveRequestProcessor — Isolated Unit Tests

Tests the live request processor's storage and broker-ref semantics
independently from LiveTradeExecutor and MockBrokerAdapter. Validates:
- Pending registration and storage
- Broker reference index (O(1) lookup)
- Fill and rejection marking
- Timeout detection
- Close order tracking
- Cleanup behavior

The orchestration surface (submit_open_order, submit_open_order_async,
modify_order_sync, etc.) and the worker thread are covered by the
end-to-end executor tests; this suite focuses on the inherited
AbstractPendingOrderManager storage layer extended with broker-ref tracking.
"""

from python.framework.trading_env.live.live_request_processor import LiveRequestProcessor
from python.framework.types.trading_env_types.latency_simulator_types import PendingOrderAction
from python.framework.types.live_types.live_execution_types import TimeoutConfig
from python.framework.types.trading_env_types.order_types import OrderDirection


class TestSubmitAndQuery:
    """Register pending orders and verify storage/query methods."""

    def test_register_pending_tracked(self, request_processor):
        """Registered order appears in pending orders."""
        request_processor.register_pending_open(
            order_id="ORD-001",
            symbol="BTCUSD",
            direction=OrderDirection.LONG,
            lots=0.001,
            broker_ref="MOCK-000001",
        )

        assert request_processor.has_pending_orders()
        assert request_processor.get_pending_count() == 1

    def test_register_pending_returns_order_id(self, request_processor):
        """register_pending_open() returns the order_id for chaining."""
        result = request_processor.register_pending_open(
            order_id="ORD-002",
            symbol="BTCUSD",
            direction=OrderDirection.LONG,
            lots=0.001,
            broker_ref="MOCK-000002",
        )
        assert result == "ORD-002"

    def test_broker_ref_lookup(self, request_processor):
        """Broker reference index provides O(1) lookup."""
        request_processor.register_pending_open(
            order_id="ORD-003",
            symbol="BTCUSD",
            direction=OrderDirection.SHORT,
            lots=0.01,
            broker_ref="MOCK-000003",
        )

        pending = request_processor.get_by_broker_ref("MOCK-000003")
        assert pending is not None
        assert pending.pending_order_id == "ORD-003"
        assert pending.symbol == "BTCUSD"
        assert pending.direction == OrderDirection.SHORT

    def test_unknown_broker_ref_returns_none(self, request_processor):
        """Lookup with unknown broker_ref returns None."""
        result = request_processor.get_by_broker_ref("NONEXISTENT")
        assert result is None

    def test_pending_order_has_live_fields(self, request_processor):
        """Registered order has submitted_at, broker_ref, timeout_at set."""
        request_processor.register_pending_open(
            order_id="ORD-004",
            symbol="BTCUSD",
            direction=OrderDirection.LONG,
            lots=0.001,
            broker_ref="MOCK-000004",
        )

        pending = request_processor.get_by_broker_ref("MOCK-000004")
        assert pending.timing.submitted_at is not None
        assert pending.broker_ref == "MOCK-000004"
        assert pending.timing.timeout_at is not None
        assert pending.timing.timeout_at > pending.timing.submitted_at

    def test_pending_order_action_is_open(self, request_processor):
        """Registered open order has action=OPEN."""
        request_processor.register_pending_open(
            order_id="ORD-005",
            symbol="BTCUSD",
            direction=OrderDirection.LONG,
            lots=0.001,
            broker_ref="MOCK-000005",
        )

        pending = request_processor.get_by_broker_ref("MOCK-000005")
        assert pending.order_action == PendingOrderAction.OPEN


class TestMarkFilled:
    """Mark orders as filled and verify removal from pending."""

    def test_mark_filled_returns_pending_order(self, request_processor):
        """mark_filled() returns the PendingOrder for fill processing."""
        request_processor.register_pending_open(
            order_id="ORD-010",
            symbol="BTCUSD",
            direction=OrderDirection.LONG,
            lots=0.001,
            broker_ref="MOCK-000010",
        )

        filled = request_processor.mark_filled(
            broker_ref="MOCK-000010",
            fill_price=50000.0,
            filled_lots=0.001,
        )

        assert filled is not None
        assert filled.pending_order_id == "ORD-010"
        assert filled.symbol == "BTCUSD"

    def test_mark_filled_removes_from_pending(self, request_processor):
        """Filled order is no longer in pending storage."""
        request_processor.register_pending_open(
            order_id="ORD-011",
            symbol="BTCUSD",
            direction=OrderDirection.LONG,
            lots=0.001,
            broker_ref="MOCK-000011",
        )

        request_processor.mark_filled(
            broker_ref="MOCK-000011",
            fill_price=50000.0,
            filled_lots=0.001,
        )

        assert not request_processor.has_pending_orders()
        assert request_processor.get_by_broker_ref("MOCK-000011") is None

    def test_mark_filled_unknown_ref_returns_none(self, request_processor):
        """mark_filled() with unknown broker_ref returns None."""
        result = request_processor.mark_filled(
            broker_ref="NONEXISTENT",
            fill_price=50000.0,
            filled_lots=0.001,
        )
        assert result is None


class TestMarkRejected:
    """Mark orders as rejected and verify removal from pending."""

    def test_mark_rejected_returns_pending_order(self, request_processor):
        """mark_rejected() returns the PendingOrder for rejection recording."""
        request_processor.register_pending_open(
            order_id="ORD-020",
            symbol="BTCUSD",
            direction=OrderDirection.LONG,
            lots=0.001,
            broker_ref="MOCK-000020",
        )

        rejected = request_processor.mark_rejected(
            broker_ref="MOCK-000020",
            reason="insufficient_margin",
        )

        assert rejected is not None
        assert rejected.pending_order_id == "ORD-020"

    def test_mark_rejected_removes_from_pending(self, request_processor):
        """Rejected order is no longer in pending storage."""
        request_processor.register_pending_open(
            order_id="ORD-021",
            symbol="BTCUSD",
            direction=OrderDirection.LONG,
            lots=0.001,
            broker_ref="MOCK-000021",
        )

        request_processor.mark_rejected(
            broker_ref="MOCK-000021",
            reason="broker_error",
        )

        assert not request_processor.has_pending_orders()

    def test_mark_rejected_unknown_ref_returns_none(self, request_processor):
        """mark_rejected() with unknown broker_ref returns None."""
        result = request_processor.mark_rejected(
            broker_ref="NONEXISTENT",
            reason="unknown",
        )
        assert result is None


class TestTimeoutDetection:
    """Timeout detection for unresponsive orders."""

    def test_no_timeouts_within_window(self, request_processor):
        """Orders within timeout window are not flagged."""
        request_processor.register_pending_open(
            order_id="ORD-030",
            symbol="BTCUSD",
            direction=OrderDirection.LONG,
            lots=0.001,
            broker_ref="MOCK-000030",
        )

        timed_out = request_processor.check_timeouts()
        assert len(timed_out) == 0

    def test_timeout_detected_after_expiry(self, logger):
        """Orders past timeout_at are detected by check_timeouts()."""
        # Use 0-second timeout so order is immediately expired
        fast_timeout = TimeoutConfig(order_timeout_seconds=0.0)
        processor = LiveRequestProcessor(logger=logger, timeout_config=fast_timeout)

        processor.register_pending_open(
            order_id="ORD-031",
            symbol="BTCUSD",
            direction=OrderDirection.LONG,
            lots=0.001,
            broker_ref="MOCK-000031",
        )

        timed_out = processor.check_timeouts()
        assert len(timed_out) == 1
        assert timed_out[0].pending_order_id == "ORD-031"

    def test_timeout_does_not_remove_order(self, logger):
        """check_timeouts() returns but does NOT remove expired orders."""
        fast_timeout = TimeoutConfig(order_timeout_seconds=0.0)
        processor = LiveRequestProcessor(logger=logger, timeout_config=fast_timeout)

        processor.register_pending_open(
            order_id="ORD-032",
            symbol="BTCUSD",
            direction=OrderDirection.LONG,
            lots=0.001,
            broker_ref="MOCK-000032",
        )

        processor.check_timeouts()
        # Order still in pending — caller decides how to handle
        assert processor.has_pending_orders()


class TestCloseOrderTracking:
    """Close order registration and pending close detection."""

    def test_register_pending_close_tracked(self, request_processor):
        """Close order appears in pending orders."""
        request_processor.register_pending_close(
            position_id="POS-001",
            broker_ref="MOCK-CLOSE-001",
            close_lots=0.001,
        )

        assert request_processor.has_pending_orders()
        assert request_processor.is_pending_close("POS-001")

    def test_close_order_action_is_close(self, request_processor):
        """Close order has action=CLOSE."""
        request_processor.register_pending_close(
            position_id="POS-002",
            broker_ref="MOCK-CLOSE-002",
            close_lots=0.001,
        )

        pending = request_processor.get_by_broker_ref("MOCK-CLOSE-002")
        assert pending.order_action == PendingOrderAction.CLOSE

    def test_is_pending_close_false_for_open(self, request_processor):
        """is_pending_close() returns False for open orders."""
        request_processor.register_pending_open(
            order_id="ORD-040",
            symbol="BTCUSD",
            direction=OrderDirection.LONG,
            lots=0.001,
            broker_ref="MOCK-000040",
        )

        assert not request_processor.is_pending_close("ORD-040")


class TestClearPending:
    """Cleanup behavior for clear_pending()."""

    def test_clear_removes_all_orders(self, request_processor):
        """clear_pending() removes all pending orders."""
        request_processor.register_pending_open(
            order_id="ORD-050",
            symbol="BTCUSD",
            direction=OrderDirection.LONG,
            lots=0.001,
            broker_ref="MOCK-000050",
        )
        request_processor.register_pending_open(
            order_id="ORD-051",
            symbol="BTCUSD",
            direction=OrderDirection.SHORT,
            lots=0.002,
            broker_ref="MOCK-000051",
        )

        request_processor.clear_pending()

        assert not request_processor.has_pending_orders()
        assert request_processor.get_pending_count() == 0

    def test_clear_also_clears_broker_ref_index(self, request_processor):
        """clear_pending() clears the broker_ref index."""
        request_processor.register_pending_open(
            order_id="ORD-052",
            symbol="BTCUSD",
            direction=OrderDirection.LONG,
            lots=0.001,
            broker_ref="MOCK-000052",
        )

        request_processor.clear_pending()

        assert request_processor.get_by_broker_ref("MOCK-000052") is None
