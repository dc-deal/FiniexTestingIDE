"""
Async Submit Path — Regression Tests for LiveRequestProcessor

Locks down the SHAPE of the async submit lifecycle introduced by #319 step 6.
test_live_executor_mock.py covers outcomes — these tests assert the lifecycle
itself so a regression to a sync-via-shortcut (which would pass outcome tests)
cannot slip through:

- open_order() returns PENDING immediately, position_id is None
- pending exists in the processor with broker_ref=None during the in-flight window
- drain_inbox() on the next tick confirms broker_ref and dispatches the fill
- The multi-listener outcome chain runs on the main thread post-drain

Tracked by #321.
"""

from datetime import datetime, timezone

from python.framework.testing.mock_adapter import MockExecutionMode
from python.framework.testing.mock_order_execution import MockOrderExecution
from python.framework.trading_env.order_guard import OrderGuard
from python.framework.types.live_types.live_execution_types import TimeoutConfig
from python.framework.types.trading_env_types.order_types import (
    OpenOrderRequest,
    OrderDirection,
    OrderStatus,
    OrderType,
    RejectionReason,
)


class TestAsyncSubmitInstantFill:
    """Initial submit returns PENDING; fill arrives via drain_inbox on next tick."""

    def test_async_submit_instant_fill_returns_pending(self, mock_instant, executor_instant):
        """Initial open_order() returns PENDING with position_id=None even in INSTANT_FILL mode.

        The fill is synthesized by the worker but only applied to the executor
        state by drain_inbox on the next tick. Until then, the caller sees the
        pending shape.
        """
        mock_instant.feed_tick(executor_instant, bid=49999.0, ask=50001.0)

        result = executor_instant.open_order(OpenOrderRequest(
            symbol="BTCUSD", order_type=OrderType.MARKET,
            direction=OrderDirection.LONG, lots=0.001,
        ))

        assert result.status == OrderStatus.PENDING
        assert result.position_id is None
        assert executor_instant.has_pending_orders()

    def test_async_submit_instant_fill_creates_position_after_tick(self, mock_instant, executor_instant):
        """Second feed_tick drains the inbox: position appears, history has EXECUTED."""
        mock_instant.feed_tick(executor_instant, bid=49999.0, ask=50001.0)

        executor_instant.open_order(OpenOrderRequest(
            symbol="BTCUSD", order_type=OrderType.MARKET,
            direction=OrderDirection.LONG, lots=0.001,
        ))
        mock_instant.feed_tick(executor_instant, bid=49999.0, ask=50001.0)

        positions = executor_instant.get_open_positions()
        assert len(positions) == 1
        assert not executor_instant.has_pending_orders()

        history = executor_instant.get_order_history()
        assert any(h.status == OrderStatus.EXECUTED for h in history)


class TestAsyncSubmitRejection:
    """Broker rejection delivered via drain_inbox advances the rejected counter."""

    def test_async_submit_reject_all_pending_then_rejected(self, mock_reject, executor_reject):
        """Initial PENDING, drain delivers REJECTED — counter increments, no position."""
        mock_reject.feed_tick(executor_reject, bid=49999.0, ask=50001.0)

        result = executor_reject.open_order(OpenOrderRequest(
            symbol="BTCUSD", order_type=OrderType.MARKET,
            direction=OrderDirection.LONG, lots=0.001,
        ))
        # Initial async return is PENDING — rejection arrives via drain
        assert result.status == OrderStatus.PENDING

        rejected_before = executor_reject.get_execution_stats().orders_rejected
        mock_reject.feed_tick(executor_reject, bid=49999.0, ask=50001.0)
        rejected_after = executor_reject.get_execution_stats().orders_rejected

        assert rejected_after == rejected_before + 1
        assert len(executor_reject.get_open_positions()) == 0
        assert not executor_reject.has_pending_orders()

    def test_async_reject_triggers_outcome_listener_chain(self, mock_reject, executor_reject):
        """An OrderGuard registered as outcome listener enters cooldown after async reject.

        Validates the multi-listener flow end-to-end: rejection delivered by
        drain_inbox → AbstractTradeExecutor._notify_outcome → registered listener.
        OrderGuard is the canonical first listener; #151 Reconciliation will
        register as a second one.
        """
        guard = OrderGuard(cooldown_seconds=60.0, max_consecutive_rejections=1)

        def listener(direction, result, pending=None):
            if result.is_rejected and result.rejection_reason == RejectionReason.BROKER_ERROR:
                guard.record_rejection(direction, datetime.now(timezone.utc))

        executor_reject.add_order_outcome_listener(listener)

        mock_reject.feed_tick(executor_reject, bid=49999.0, ask=50001.0)
        executor_reject.open_order(OpenOrderRequest(
            symbol="BTCUSD", order_type=OrderType.MARKET,
            direction=OrderDirection.LONG, lots=0.001,
        ))
        # No cooldown yet — rejection hasn't drained
        assert not guard.is_direction_blocked(OrderDirection.LONG, datetime.now(timezone.utc))

        mock_reject.feed_tick(executor_reject, bid=49999.0, ask=50001.0)

        # Listener fired through the drain — cooldown now active
        assert guard.is_direction_blocked(OrderDirection.LONG, datetime.now(timezone.utc))


class TestAsyncSubmitDelayedFill:
    """Two-tick lifecycle: tick1 confirms broker_ref, tick2 polls and fills."""

    def test_async_submit_delayed_fill_two_tick_lifecycle(self, mock_delayed, executor_delayed):
        """Drain confirms broker_ref while order stays pending; next tick polling fills it.

        Phase 0 (drain_inbox) and Phase 1 (query polling) run on the same tick in
        production, so the mock fills in one tick. await_submit_confirmation
        isolates the drain step to verify the broker_ref confirms WITHOUT the
        order filling — which is the exact intermediate state #319 step 6
        introduced.
        """
        mock_delayed.feed_tick(executor_delayed, bid=49999.0, ask=50001.0)

        result = executor_delayed.open_order(OpenOrderRequest(
            symbol="BTCUSD", order_type=OrderType.MARKET,
            direction=OrderDirection.LONG, lots=0.001,
        ))
        order_id = result.order_id

        # Drain-only step: broker_ref confirmed, no Phase-1 polling fires
        mock_delayed.await_submit_confirmation(executor_delayed)
        assert executor_delayed.has_pending_orders()
        assert len(executor_delayed.get_open_positions()) == 0
        pending = next(
            p for p in executor_delayed._request_processor.get_pending_orders()
            if p.pending_order_id == order_id
        )
        assert pending.broker_ref is not None

        # Real tick: Phase-1 polling calls query_order_sync → DELAYED_FILL flips to FILLED
        mock_delayed.feed_tick(executor_delayed, bid=50050.0, ask=50052.0)
        assert not executor_delayed.has_pending_orders()
        assert len(executor_delayed.get_open_positions()) == 1

    def test_async_submit_broker_ref_set_post_confirmation(self, mock_delayed, executor_delayed):
        """broker_ref is None immediately after submit, matches MOCK-NNNNNN after drain."""
        mock_delayed.feed_tick(executor_delayed, bid=49999.0, ask=50001.0)

        result = executor_delayed.open_order(OpenOrderRequest(
            symbol="BTCUSD", order_type=OrderType.MARKET,
            direction=OrderDirection.LONG, lots=0.001,
        ))
        order_id = result.order_id

        # In-flight window: pending exists, broker_ref still None
        pending_pre = executor_delayed._request_processor.get_pending_orders()
        target = next(p for p in pending_pre if p.pending_order_id == order_id)
        assert target.broker_ref is None

        # Drain without triggering Phase-2 polling (no on_tick) so the order
        # stays pending while we inspect the confirmed broker_ref
        mock_delayed.await_submit_confirmation(executor_delayed)

        pending_post = executor_delayed._request_processor.get_pending_orders()
        target = next(p for p in pending_post if p.pending_order_id == order_id)
        assert target.broker_ref is not None
        assert target.broker_ref.startswith("MOCK-")


class TestAsyncSubmitClose:
    """Async close_position lifecycle: PENDING then FILLED on next tick drain."""

    def test_async_submit_close_position_async(self, mock_instant, executor_instant):
        """Close returns PENDING; next tick drains the close fill and clears the position."""
        # Open + drain fill
        mock_instant.feed_tick(executor_instant, bid=49999.0, ask=50001.0)
        executor_instant.open_order(OpenOrderRequest(
            symbol="BTCUSD", order_type=OrderType.MARKET,
            direction=OrderDirection.LONG, lots=0.001,
        ))
        mock_instant.feed_tick(executor_instant, bid=49999.0, ask=50001.0)
        positions = executor_instant.get_open_positions()
        assert len(positions) == 1
        position_id = positions[0].position_id

        close_result = executor_instant.close_position(position_id)
        assert close_result.status == OrderStatus.PENDING
        assert close_result.position_id is None
        assert executor_instant.has_pending_orders()

        mock_instant.feed_tick(executor_instant, bid=50100.0, ask=50102.0)
        assert len(executor_instant.get_open_positions()) == 0
        assert not executor_instant.has_pending_orders()


class TestAsyncSubmitShutdown:
    """Worker shutdown during in-flight pending records FORCE_CLOSED cleanly."""

    def test_async_worker_shutdown_during_pending(self):
        """Submit, then close_all_remaining_orders before drain: clean shutdown, FORCE_CLOSED recorded.

        Uses DELAYED_FILL so the order stays pending (Phase-1 poll would fill
        in INSTANT mode and the shutdown path wouldn't see a stuck pending).
        """
        mock = MockOrderExecution(mode=MockExecutionMode.DELAYED_FILL)
        executor = mock.create_executor()
        mock.feed_tick(executor, bid=49999.0, ask=50001.0)

        executor.open_order(OpenOrderRequest(
            symbol="BTCUSD", order_type=OrderType.MARKET,
            direction=OrderDirection.LONG, lots=0.001,
        ))
        # Confirm broker_ref but do NOT trigger Phase-2 polling — we want the
        # order to still be in pending when shutdown fires
        mock.await_submit_confirmation(executor)
        assert executor.has_pending_orders()

        executor.close_all_remaining_orders(current_msc=0)

        # Shutdown completed: no pending, worker thread joined
        assert not executor.has_pending_orders()
        assert not executor._request_processor._worker_running


class TestAsyncSubmitTimeout:
    """TIMEOUT-mode pending eventually triggers timeout rejection via check_timeouts."""

    def test_async_submit_timeout_mode(self):
        """Submit in TIMEOUT mode stays pending; tick-driven check_timeouts triggers rejection."""
        # Short timeout so the test runs fast
        mock = MockOrderExecution(mode=MockExecutionMode.TIMEOUT, timeout_seconds=0.1)
        executor = mock.create_executor()
        mock.feed_tick(executor, bid=49999.0, ask=50001.0)

        executor.open_order(OpenOrderRequest(
            symbol="BTCUSD", order_type=OrderType.MARKET,
            direction=OrderDirection.LONG, lots=0.001,
        ))
        mock.await_submit_confirmation(executor)
        assert executor.has_pending_orders()

        # Wait past the timeout window
        import time
        time.sleep(0.15)

        rejected_before = executor.get_execution_stats().orders_rejected
        mock.feed_tick(executor, bid=49999.0, ask=50001.0)
        rejected_after = executor.get_execution_stats().orders_rejected

        # Timeout path increments orders_rejected and clears the pending
        assert rejected_after > rejected_before
        assert not executor.has_pending_orders()


class TestAsyncSubmitMultiple:
    """Multiple async submits in flight, single drain tick fills all of them."""

    def test_async_submit_multiple_orders(self, mock_instant, executor_instant):
        """Three async submits back-to-back, one drain tick, three positions exist."""
        mock_instant.feed_tick(executor_instant, bid=49999.0, ask=50001.0)

        for i in range(3):
            result = executor_instant.open_order(OpenOrderRequest(
                symbol="BTCUSD", order_type=OrderType.MARKET,
                direction=OrderDirection.LONG, lots=0.001,
                comment=f"order_{i}",
            ))
            assert result.status == OrderStatus.PENDING
            assert result.position_id is None

        mock_instant.feed_tick(executor_instant, bid=49999.0, ask=50001.0)

        positions = executor_instant.get_open_positions()
        assert len(positions) == 3
        assert not executor_instant.has_pending_orders()
