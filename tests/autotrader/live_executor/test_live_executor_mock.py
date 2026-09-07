"""
LiveTradeExecutor + MockBrokerAdapter — Integration Tests

Tests the full execution pipeline: open_order() → broker response →
fill processing → order_history / portfolio update.

Each test creates a fresh executor via conftest fixtures.
MockOrderExecution provides tick feeding for pending order processing.
"""

from python.framework.testing.mock_broker_adapter import MockExecutionMode
from python.framework.types.trading_env_types.order_types import (
    OpenOrderRequest,
    OrderDirection,
    OrderStatus,
    OrderType,
    RejectionReason,
)


class TestInstantFill:
    """INSTANT_FILL mode: orders fill on the next tick after async submit.

    Async submit (#319 step 6) routes the order through the worker thread.
    open_order() returns PENDING immediately; the fill is applied by
    drain_inbox() on the next on_tick call. feed_tick() flushes the
    outbox before delivering the tick, so the pattern submit → feed_tick
    → assert is deterministic.
    """

    def test_open_order_returns_pending_initially(self, mock_instant, executor_instant):
        """open_order() returns PENDING immediately under async submit."""
        mock_instant.feed_tick(executor_instant, bid=49999.0, ask=50001.0)

        result = executor_instant.open_order(OpenOrderRequest(
            symbol='BTCUSD', order_type=OrderType.MARKET, direction=OrderDirection.LONG, lots=0.001
        ))

        # Initial return is PENDING — broker_ref is set later via drain_inbox
        assert result.status == OrderStatus.PENDING
        assert result.position_id is None

    def test_instant_fill_creates_position(self, mock_instant, executor_instant):
        """Instant fill creates an open position after the next tick drain."""
        mock_instant.feed_tick(executor_instant, bid=49999.0, ask=50001.0)

        executor_instant.open_order(OpenOrderRequest(
            symbol='BTCUSD', order_type=OrderType.MARKET, direction=OrderDirection.LONG, lots=0.001
        ))
        # Next tick: flush_outbox + drain_inbox applies the fill
        mock_instant.feed_tick(executor_instant, bid=49999.0, ask=50001.0)

        positions = executor_instant.get_open_positions()
        assert len(positions) == 1
        assert positions[0].symbol == 'BTCUSD'
        assert positions[0].direction == OrderDirection.LONG

    def test_order_history_recorded(self, mock_instant, executor_instant):
        """Executed order appears in order history (after fill drain)."""
        mock_instant.feed_tick(executor_instant, bid=49999.0, ask=50001.0)

        executor_instant.open_order(OpenOrderRequest(
            symbol='BTCUSD', order_type=OrderType.MARKET, direction=OrderDirection.LONG, lots=0.001
        ))
        mock_instant.feed_tick(executor_instant, bid=49999.0, ask=50001.0)

        history = executor_instant.get_order_history()
        # History contains initial PENDING + final EXECUTED entry
        assert any(h.status == OrderStatus.EXECUTED for h in history)

    def test_no_pending_after_instant_fill(self, mock_instant, executor_instant):
        """Pending clears once the worker fill is drained."""
        mock_instant.feed_tick(executor_instant, bid=49999.0, ask=50001.0)

        executor_instant.open_order(OpenOrderRequest(
            symbol='BTCUSD', order_type=OrderType.MARKET, direction=OrderDirection.LONG, lots=0.001
        ))
        mock_instant.feed_tick(executor_instant, bid=49999.0, ask=50001.0)

        assert not executor_instant.has_pending_orders()


class TestInstantFillClose:
    """INSTANT_FILL mode: close_position() also routes through async submit."""

    def test_close_position_returns_pending(self, mock_instant, executor_instant):
        """close_position() returns PENDING immediately; fill on next tick."""
        mock_instant.feed_tick(executor_instant, bid=49999.0, ask=50001.0)

        executor_instant.open_order(OpenOrderRequest(
            symbol='BTCUSD', order_type=OrderType.MARKET, direction=OrderDirection.LONG, lots=0.001
        ))
        # Drain open fill so the position exists
        mock_instant.feed_tick(executor_instant, bid=49999.0, ask=50001.0)

        positions = executor_instant.get_open_positions()
        position_id = positions[0].position_id

        # Feed new tick at different price for close
        mock_instant.feed_tick(executor_instant, bid=50100.0, ask=50102.0)

        close_result = executor_instant.close_position(position_id)
        # Initial close result is PENDING — fill applied on next tick
        assert close_result.status == OrderStatus.PENDING

    def test_close_removes_from_open_positions(self, mock_instant, executor_instant):
        """Closed position no longer appears in open positions after fill drain."""
        mock_instant.feed_tick(executor_instant, bid=49999.0, ask=50001.0)

        executor_instant.open_order(OpenOrderRequest(
            symbol='BTCUSD', order_type=OrderType.MARKET, direction=OrderDirection.LONG, lots=0.001
        ))
        mock_instant.feed_tick(executor_instant, bid=49999.0, ask=50001.0)

        positions = executor_instant.get_open_positions()
        position_id = positions[0].position_id

        mock_instant.feed_tick(executor_instant, bid=50100.0, ask=50102.0)
        executor_instant.close_position(position_id)
        mock_instant.feed_tick(executor_instant, bid=50100.0, ask=50102.0)

        assert len(executor_instant.get_open_positions()) == 0


class TestDelayedFill:
    """DELAYED_FILL mode: orders go PENDING, fill on next broker poll."""

    def test_open_order_returns_pending(self, mock_delayed, executor_delayed):
        """open_order() returns PENDING in delayed fill mode."""
        mock_delayed.feed_tick(executor_delayed, bid=49999.0, ask=50001.0)

        result = executor_delayed.open_order(OpenOrderRequest(
            symbol='BTCUSD', order_type=OrderType.MARKET, direction=OrderDirection.LONG, lots=0.001
        ))

        # Async submit (#319 step 6): result is PENDING and broker_ref is
        # not yet known. The broker_ref gets confirmed on the next tick
        # via drain_inbox.
        assert result.status == OrderStatus.PENDING
        assert result.position_id is None

    def test_pending_order_tracked(self, mock_delayed, executor_delayed):
        """Delayed order is tracked as pending."""
        mock_delayed.feed_tick(executor_delayed, bid=49999.0, ask=50001.0)

        executor_delayed.open_order(OpenOrderRequest(
            symbol='BTCUSD', order_type=OrderType.MARKET, direction=OrderDirection.LONG, lots=0.001
        ))

        assert executor_delayed.has_pending_orders()

    def test_delayed_fill_on_next_tick(self, mock_delayed, executor_delayed):
        """Pending order fills when next tick triggers _process_pending_orders()."""
        mock_delayed.feed_tick(executor_delayed, bid=49999.0, ask=50001.0)

        executor_delayed.open_order(OpenOrderRequest(
            symbol='BTCUSD', order_type=OrderType.MARKET, direction=OrderDirection.LONG, lots=0.001
        ))

        # Next tick triggers on_tick() → _process_pending_orders() → broker poll → fill
        mock_delayed.feed_tick(executor_delayed, bid=50050.0, ask=50052.0)

        positions = executor_delayed.get_open_positions()
        assert len(positions) == 1
        assert not executor_delayed.has_pending_orders()


class TestRejection:
    """REJECT_ALL mode: all orders are rejected by broker.

    With async submit (#319 step 6), the initial open_order return is
    PENDING — the REJECTED outcome lands in order_history once the worker
    response is drained on the next tick.
    """

    def test_rejected_outcome_in_history(self, mock_reject, executor_reject):
        """Rejection appears in order history after the next tick drains the worker."""
        mock_reject.feed_tick(executor_reject, bid=49999.0, ask=50001.0)

        executor_reject.open_order(OpenOrderRequest(
            symbol='BTCUSD', order_type=OrderType.MARKET, direction=OrderDirection.LONG, lots=0.001
        ))
        # Drain worker reject into order_history
        mock_reject.feed_tick(executor_reject, bid=49999.0, ask=50001.0)

        history = executor_reject.get_order_history()
        rejected = [h for h in history if h.status == OrderStatus.REJECTED]
        assert len(rejected) >= 1

    def test_no_position_created_on_rejection(self, mock_reject, executor_reject):
        """Rejected order does not create any position."""
        mock_reject.feed_tick(executor_reject, bid=49999.0, ask=50001.0)

        executor_reject.open_order(OpenOrderRequest(
            symbol='BTCUSD', order_type=OrderType.MARKET, direction=OrderDirection.LONG, lots=0.001
        ))
        mock_reject.feed_tick(executor_reject, bid=49999.0, ask=50001.0)

        assert len(executor_reject.get_open_positions()) == 0


class TestFeatureGating:
    """
    Feature gating: MARKET, LIMIT, STOP and STOP_LIMIT are routable; nothing else is.

    These two tests used to assert that STOP and STOP_LIMIT were REFUSED, which was the
    live path's honest state until #500 taught every layer between the strategy and the
    wire to carry them. What survives is the shape of the guarantee: a type the pipeline
    has not built comes back ORDER_TYPE_NOT_SUPPORTED, and a conditional order with no
    usable trigger comes back INVALID_PRICE rather than reaching a payload builder.
    """

    def test_a_type_the_pipeline_has_not_built_is_rejected(self, mock_instant, executor_instant):
        """ICEBERG: the venue offers it, nothing here places one."""
        mock_instant.feed_tick(executor_instant, bid=49999.0, ask=50001.0)

        result = executor_instant.open_order(OpenOrderRequest(
            symbol='BTCUSD', order_type=OrderType.ICEBERG,
            direction=OrderDirection.LONG, lots=0.001
        ))

        assert result.status == OrderStatus.REJECTED
        assert result.rejection_reason == RejectionReason.ORDER_TYPE_NOT_SUPPORTED

    def test_a_stop_without_a_trigger_is_rejected_on_the_price(
            self, mock_instant, executor_instant):
        """
        The type is routable now, so the refusal moves to the price — and it must.

        Live had NO price validation of any kind before #500 (`order_guard` does not know
        the field), so a stop with no trigger would have reached the payload builder and
        the venue's own answer would have been the first thing to notice.
        """
        mock_instant.feed_tick(executor_instant, bid=49999.0, ask=50001.0)

        result = executor_instant.open_order(OpenOrderRequest(
            symbol='BTCUSD', order_type=OrderType.STOP,
            direction=OrderDirection.LONG, lots=0.001
        ))

        assert result.status == OrderStatus.REJECTED
        assert result.rejection_reason == RejectionReason.INVALID_PRICE

    def test_a_stop_limit_without_a_limit_price_is_rejected_too(
            self, mock_instant, executor_instant):
        """A stop-limit needs BOTH prices — the trigger alone is not enough."""
        mock_instant.feed_tick(executor_instant, bid=49999.0, ask=50001.0)

        result = executor_instant.open_order(OpenOrderRequest(
            symbol='BTCUSD', order_type=OrderType.STOP_LIMIT,
            direction=OrderDirection.LONG, lots=0.001, stop_price=51000.0
        ))

        assert result.status == OrderStatus.REJECTED
        assert result.rejection_reason == RejectionReason.INVALID_PRICE

    def test_a_fully_formed_stop_is_accepted_and_rests(self, mock_instant, executor_instant):
        """
        The capability this issue exists for: a stop entry can be placed live.

        It rests rather than filling — the venue holds the trigger — so the observable is
        the executor's own resting-order count, not a position.
        """
        mock_instant.feed_tick(executor_instant, bid=49999.0, ask=50001.0)

        result = executor_instant.open_order(OpenOrderRequest(
            symbol='BTCUSD', order_type=OrderType.STOP,
            direction=OrderDirection.LONG, lots=0.001, stop_price=51000.0
        ))

        assert result.status == OrderStatus.PENDING, (
            f'A declared stop must be accepted, got {result.rejection_reason}')
        counts = executor_instant.get_active_order_counts()
        assert counts['active_stops'] == 1, (
            f'A live stop rests in the stop world, not among the limits: {counts}')
        assert counts['active_limits'] == 0


class TestValidation:
    """Order validation against broker limits."""

    def test_invalid_symbol_rejected(self, mock_instant, executor_instant):
        """Order for unknown symbol is rejected."""
        mock_instant.feed_tick(executor_instant, bid=49999.0, ask=50001.0)

        result = executor_instant.open_order(OpenOrderRequest(
            symbol='INVALID_SYMBOL', order_type=OrderType.MARKET, direction=OrderDirection.LONG, lots=0.001
        ))

        assert result.status == OrderStatus.REJECTED

    def test_close_nonexistent_position_rejected(self, mock_instant, executor_instant):
        """Closing non-existent position returns REJECTED."""
        result = executor_instant.close_position('NONEXISTENT-POS')

        assert result.status == OrderStatus.REJECTED


class TestExecutionStats:
    """Execution statistics consistency."""

    def test_stats_after_instant_fill(self, mock_instant, executor_instant):
        """Execution stats reflect completed order after fill drain."""
        mock_instant.feed_tick(executor_instant, bid=49999.0, ask=50001.0)

        executor_instant.open_order(OpenOrderRequest(
            symbol='BTCUSD', order_type=OrderType.MARKET, direction=OrderDirection.LONG, lots=0.001
        ))
        # Next tick: drain worker response → executor stats update
        mock_instant.feed_tick(executor_instant, bid=49999.0, ask=50001.0)

        stats = executor_instant.get_execution_stats()
        assert stats.orders_sent == 1
        assert stats.orders_executed >= 1

    def test_stats_after_rejection(self, mock_reject, executor_reject):
        """Execution stats count rejections after drain."""
        mock_reject.feed_tick(executor_reject, bid=49999.0, ask=50001.0)

        executor_reject.open_order(OpenOrderRequest(
            symbol='BTCUSD', order_type=OrderType.MARKET, direction=OrderDirection.LONG, lots=0.001
        ))
        # Drain async rejection into rejection counter
        mock_reject.feed_tick(executor_reject, bid=49999.0, ask=50001.0)

        stats = executor_reject.get_execution_stats()
        assert stats.orders_sent == 1
        assert stats.orders_rejected >= 1


class TestNotLiveCapable:
    """Adapter that is not live-capable cannot create LiveTradeExecutor."""

    def test_non_live_adapter_raises(self):
        """LiveTradeExecutor raises ValueError for non-live adapter."""
        from python.framework.logging.global_logger import GlobalLogger
        from python.framework.testing.mock_broker_adapter import MockBrokerAdapter
        from python.framework.trading_env.broker_config import BrokerConfig
        from python.framework.types.trading_env_types.broker_types import BrokerType

        adapter = MockBrokerAdapter(mode=MockExecutionMode.INSTANT_FILL)
        # Monkey-patch to simulate non-live adapter
        adapter.is_live_capable = lambda: False

        broker_config = BrokerConfig(BrokerType.KRAKEN_SPOT, adapter)
        logger = GlobalLogger(name='TestNonLive')

        import pytest
        with pytest.raises(ValueError, match='not live-capable'):
            from python.framework.trading_env.live.live_trade_executor import LiveTradeExecutor
            LiveTradeExecutor(
                broker_config=broker_config,
                initial_balance=10000.0,
                account_currency='USD',
                logger=logger,
            )
