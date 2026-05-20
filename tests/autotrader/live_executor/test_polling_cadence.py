"""
Live Polling Cadence — Regression Tests (#320)

Locks down the three structural fixes introduced by #320:

1. Heartbeat — side-effect-free drain in idle ticks. Drains async worker
   responses + processes timeouts without touching tick state. Sim is no-op.
2. Async polling — _process_active_orders schedules QueryJobs via the worker
   thread rather than blocking the main thread on query_order_sync.
3. In-flight guard + per-order throttle — each active LIMIT is polled at
   most once per poll_interval_ms, and only one outstanding query per order.

All tests target the LIMIT-order polling path (active_limit_orders); MARKET
polling stays sync and is out of scope for this issue.
"""

import time
from datetime import datetime, timedelta, timezone

from python.framework.logging.global_logger import GlobalLogger
from python.framework.testing.mock_adapter import MockBrokerAdapter, MockExecutionMode
from python.framework.trading_env.broker_config import BrokerConfig
from python.framework.trading_env.live.live_trade_executor import LiveTradeExecutor
from python.framework.types.live_types.live_execution_types import (
    BrokerOrderStatus,
    BrokerResponse,
    TimeoutConfig,
)
from python.framework.types.live_types.live_request_types import QueryResponse
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.trading_env_types.broker_types import BrokerType
from python.framework.types.trading_env_types.order_types import (
    OpenOrderRequest,
    OrderDirection,
    OrderType,
)


# =============================================================================
# Helpers
# =============================================================================


def _submit_limit_and_confirm(mock, executor, price=49000.0, lots=0.001):
    """Submit a LIMIT order and confirm broker_ref via drain (no Phase-2 fill)."""
    mock.feed_tick(executor, bid=49999.0, ask=50001.0)
    result = executor.open_order(OpenOrderRequest(
        symbol="BTCUSD", order_type=OrderType.LIMIT,
        direction=OrderDirection.LONG, lots=lots, price=price,
    ))
    mock.await_submit_confirmation(executor)
    return result.order_id


def _build_executor_with_poll_interval(mode: MockExecutionMode, poll_interval_ms: int) -> LiveTradeExecutor:
    """Build a fresh executor with an explicit poll_interval_ms (mock infra)."""
    adapter = MockBrokerAdapter(mode=mode)
    broker_config = BrokerConfig(BrokerType.KRAKEN_SPOT, adapter)
    return LiveTradeExecutor(
        broker_config=broker_config,
        initial_balance=10000.0,
        account_currency='USD',
        logger=GlobalLogger(name='PollingCadenceTest'),
        timeout_config=TimeoutConfig(order_timeout_seconds=30.0),
        poll_interval_ms=poll_interval_ms,
    )


# =============================================================================
# 1. Heartbeat — side-effect-free drain
# =============================================================================


class TestHeartbeat:
    """heartbeat() drains inbox + checks timeouts without mutating tick state."""

    def test_heartbeat_drains_inbox_without_tick_state(self, mock_instant, executor_instant):
        """heartbeat() does not bump _tick_counter or touch current_tick."""
        mock_instant.feed_tick(executor_instant, bid=50000.0, ask=50001.0)
        tick_counter_before = executor_instant._tick_counter
        current_tick_before = executor_instant._current_tick

        executor_instant.heartbeat()

        assert executor_instant._tick_counter == tick_counter_before
        assert executor_instant._current_tick is current_tick_before

    def test_heartbeat_processes_timeouts(self, mock_timeout, executor_timeout):
        """A pending order past its timeout is rejected when heartbeat fires."""
        mock_timeout.feed_tick(executor_timeout, bid=50000.0, ask=50001.0)

        executor_timeout.open_order(OpenOrderRequest(
            symbol='BTCUSD', order_type=OrderType.MARKET,
            direction=OrderDirection.LONG, lots=0.001,
        ))
        mock_timeout.await_submit_confirmation(executor_timeout)

        # Force timeout: backdate timeout_at on every pending order
        past = datetime.now(timezone.utc) - timedelta(seconds=60)
        for pending in executor_timeout._request_processor.get_pending_orders():
            pending.timeout_at = past

        rejected_before = executor_timeout.get_execution_stats().orders_rejected
        executor_timeout.heartbeat()
        rejected_after = executor_timeout.get_execution_stats().orders_rejected

        assert rejected_after == rejected_before + 1

    def test_heartbeat_sim_is_noop(self):
        """TradeSimulator inherits the default no-op heartbeat — no errors, no state change."""
        from python.framework.trading_env.simulation.trade_simulator import TradeSimulator

        adapter = MockBrokerAdapter(mode=MockExecutionMode.INSTANT_FILL)
        broker_config = BrokerConfig(BrokerType.KRAKEN_SPOT, adapter)
        sim = TradeSimulator(
            broker_config=broker_config,
            initial_balance=10000.0,
            account_currency='USD',
            logger=GlobalLogger(name='SimHeartbeatTest'),
        )

        sim.on_tick(TickData(
            timestamp=datetime.now(timezone.utc),
            symbol='BTCUSD', bid=50000.0, ask=50001.0,
        ))
        tick_counter_before = sim._tick_counter

        # Inherited no-op — must not raise, must not mutate
        sim.heartbeat()
        assert sim._tick_counter == tick_counter_before


# =============================================================================
# 2. Throttle — per-order poll_interval_ms gate
# =============================================================================


class TestThrottle:
    """Each active LIMIT is dispatched at most once per poll_interval_ms."""

    def test_active_limit_polled_at_most_once_per_interval(self):
        """Many _process_active_orders calls within poll_interval_ms → at most one dispatch.

        Uses a 60s throttle so the inner flush_outbox waits (≈20-30ms each on
        slow machines) can't push the 50-iteration loop past the window.
        """
        executor = _build_executor_with_poll_interval(MockExecutionMode.TIMEOUT, poll_interval_ms=60_000)

        # Feed an initial tick so on_tick sets _current_tick / prices
        executor.on_tick(TickData(
            timestamp=datetime.now(timezone.utc),
            symbol='BTCUSD', bid=49999.0, ask=50001.0,
        ))
        result = executor.open_order(OpenOrderRequest(
            symbol='BTCUSD', order_type=OrderType.LIMIT,
            direction=OrderDirection.LONG, lots=0.001, price=49000.0,
        ))
        executor._request_processor.flush_outbox()
        executor._request_processor.drain_inbox()

        # Count dispatches by patching the async submit
        dispatches = []
        original = executor._request_processor.submit_query_order_async

        def _counting(order_id, broker_ref, adapter):
            dispatches.append(order_id)
            original(order_id, broker_ref, adapter)

        executor._request_processor.submit_query_order_async = _counting

        # Reset throttle so the first call dispatches deterministically
        for p in executor._active_limit_orders:
            p.last_polled_at_ms = 0.0
            p.in_flight_query = False

        # Many scheduler calls within < 1s
        for _ in range(50):
            executor._process_active_orders()
            # drain any in-flight so subsequent calls see in_flight=False
            executor._request_processor.flush_outbox()
            executor._request_processor.drain_inbox()

        assert len(dispatches) == 1, f"Expected 1 dispatch within throttle window, got {len(dispatches)}"

    def test_throttle_interval_configurable(self):
        """poll_interval_ms=200 → second dispatch only after sleeping past the window."""
        executor = _build_executor_with_poll_interval(MockExecutionMode.TIMEOUT, poll_interval_ms=200)

        executor.on_tick(TickData(
            timestamp=datetime.now(timezone.utc),
            symbol='BTCUSD', bid=49999.0, ask=50001.0,
        ))
        executor.open_order(OpenOrderRequest(
            symbol='BTCUSD', order_type=OrderType.LIMIT,
            direction=OrderDirection.LONG, lots=0.001, price=49000.0,
        ))
        executor._request_processor.flush_outbox()
        executor._request_processor.drain_inbox()

        dispatches = []
        original = executor._request_processor.submit_query_order_async

        def _counting(order_id, broker_ref, adapter):
            dispatches.append(time.time())
            original(order_id, broker_ref, adapter)

        executor._request_processor.submit_query_order_async = _counting

        for p in executor._active_limit_orders:
            p.last_polled_at_ms = 0.0
            p.in_flight_query = False

        executor._process_active_orders()
        executor._request_processor.flush_outbox()
        executor._request_processor.drain_inbox()
        time.sleep(0.25)  # past 200 ms window
        executor._process_active_orders()

        assert len(dispatches) == 2, f"Expected 2 dispatches across two intervals, got {len(dispatches)}"

    def test_throttle_uses_wall_clock_not_tick_time(self):
        """last_polled_at_ms is wall-clock based — tick.time_msc jumps don't bypass throttle."""
        executor = _build_executor_with_poll_interval(MockExecutionMode.TIMEOUT, poll_interval_ms=10_000)

        executor.on_tick(TickData(
            timestamp=datetime.now(timezone.utc),
            symbol='BTCUSD', bid=49999.0, ask=50001.0,
        ))
        executor.open_order(OpenOrderRequest(
            symbol='BTCUSD', order_type=OrderType.LIMIT,
            direction=OrderDirection.LONG, lots=0.001, price=49000.0,
        ))
        executor._request_processor.flush_outbox()
        executor._request_processor.drain_inbox()

        dispatches = []
        original = executor._request_processor.submit_query_order_async
        executor._request_processor.submit_query_order_async = (
            lambda order_id, broker_ref, adapter: (
                dispatches.append(order_id), original(order_id, broker_ref, adapter)
            )
        )

        for p in executor._active_limit_orders:
            p.last_polled_at_ms = 0.0
            p.in_flight_query = False

        executor._process_active_orders()
        executor._request_processor.flush_outbox()
        executor._request_processor.drain_inbox()

        # Simulate a far-future tick — must not unblock throttle (no wall-clock progression)
        future_tick = TickData(
            timestamp=datetime.now(timezone.utc) + timedelta(days=7),
            symbol='BTCUSD', bid=49999.0, ask=50001.0,
        )
        executor.on_tick(future_tick)

        assert len(dispatches) == 1, f"Future tick must not bypass wall-clock throttle, got {len(dispatches)}"


# =============================================================================
# 3. In-flight guard
# =============================================================================


class TestInFlightGuard:
    """One outstanding QueryJob per order. Cleared on every QueryResponse."""

    def test_in_flight_query_blocks_concurrent_dispatch(self):
        """If in_flight_query=True the scheduler skips silently — no second dispatch."""
        executor = _build_executor_with_poll_interval(MockExecutionMode.TIMEOUT, poll_interval_ms=0)

        executor.on_tick(TickData(
            timestamp=datetime.now(timezone.utc),
            symbol='BTCUSD', bid=49999.0, ask=50001.0,
        ))
        executor.open_order(OpenOrderRequest(
            symbol='BTCUSD', order_type=OrderType.LIMIT,
            direction=OrderDirection.LONG, lots=0.001, price=49000.0,
        ))
        executor._request_processor.flush_outbox()
        executor._request_processor.drain_inbox()

        # Manually flip the in-flight bit AFTER confirmation but before the scheduler runs
        pending = executor._active_limit_orders[0]
        pending.in_flight_query = True
        pending.last_polled_at_ms = 0.0

        dispatches = []
        original = executor._request_processor.submit_query_order_async
        executor._request_processor.submit_query_order_async = (
            lambda order_id, broker_ref, adapter: dispatches.append(order_id)
        )

        executor._process_active_orders()
        assert dispatches == []

    def test_in_flight_cleared_on_pending_response(self):
        """PENDING response → in_flight_query=False, order stays in active list."""
        executor = _build_executor_with_poll_interval(MockExecutionMode.TIMEOUT, poll_interval_ms=0)

        executor.on_tick(TickData(
            timestamp=datetime.now(timezone.utc),
            symbol='BTCUSD', bid=49999.0, ask=50001.0,
        ))
        executor.open_order(OpenOrderRequest(
            symbol='BTCUSD', order_type=OrderType.LIMIT,
            direction=OrderDirection.LONG, lots=0.001, price=49000.0,
        ))
        executor._request_processor.flush_outbox()
        executor._request_processor.drain_inbox()

        executor._process_active_orders()
        executor._request_processor.flush_outbox()
        executor._request_processor.drain_inbox()

        # TIMEOUT mode → PENDING — order remains, in_flight cleared
        assert len(executor._active_limit_orders) == 1
        assert executor._active_limit_orders[0].in_flight_query is False

    def test_in_flight_cleared_on_filled(self):
        """FILLED response → in_flight_query cleared as side effect; order removed."""
        executor = _build_executor_with_poll_interval(MockExecutionMode.DELAYED_FILL, poll_interval_ms=0)

        executor.on_tick(TickData(
            timestamp=datetime.now(timezone.utc),
            symbol='BTCUSD', bid=49999.0, ask=50001.0,
        ))
        executor.open_order(OpenOrderRequest(
            symbol='BTCUSD', order_type=OrderType.LIMIT,
            direction=OrderDirection.LONG, lots=0.001, price=49000.0,
        ))
        executor._request_processor.flush_outbox()
        executor._request_processor.drain_inbox()

        executor._process_active_orders()
        executor._request_processor.flush_outbox()
        executor._request_processor.drain_inbox()

        # DELAYED_FILL pops on first query → FILLED, order removed from active list
        assert len(executor._active_limit_orders) == 0
        assert len(executor.get_open_positions()) == 1

    def test_in_flight_cleared_on_stale_response(self):
        """Stale broker_ref response → in_flight_query cleared, state untouched."""
        executor = _build_executor_with_poll_interval(MockExecutionMode.TIMEOUT, poll_interval_ms=5000)

        executor.on_tick(TickData(
            timestamp=datetime.now(timezone.utc),
            symbol='BTCUSD', bid=49999.0, ask=50001.0,
        ))
        executor.open_order(OpenOrderRequest(
            symbol='BTCUSD', order_type=OrderType.LIMIT,
            direction=OrderDirection.LONG, lots=0.001, price=49000.0,
        ))
        executor._request_processor.flush_outbox()
        executor._request_processor.drain_inbox()

        pending = executor._active_limit_orders[0]
        order_id = pending.pending_order_id
        pending.broker_ref = 'NEW-REF'  # Simulate post-modify ref flip
        pending.in_flight_query = True

        stale = QueryResponse(
            order_id=order_id,
            broker_response=BrokerResponse(
                broker_ref='OLD-REF', status=BrokerOrderStatus.CANCELLED,
                timestamp=datetime.now(timezone.utc),
            ),
        )
        executor._handle_query_response(stale)

        # in_flight cleared, but state untouched (order still active under NEW-REF)
        assert pending.in_flight_query is False
        assert pending in executor._active_limit_orders


# =============================================================================
# 4. Stale-response guard after modify
# =============================================================================


class TestStaleResponseGuard:
    """A query dispatched against the old broker_ref is discarded after a modify swap."""

    def test_stale_query_after_modify_discarded(self):
        """Pending now carries new broker_ref; response with old ref → no state mutation."""
        executor = _build_executor_with_poll_interval(MockExecutionMode.TIMEOUT, poll_interval_ms=5000)

        executor.on_tick(TickData(
            timestamp=datetime.now(timezone.utc),
            symbol='BTCUSD', bid=49999.0, ask=50001.0,
        ))
        executor.open_order(OpenOrderRequest(
            symbol='BTCUSD', order_type=OrderType.LIMIT,
            direction=OrderDirection.LONG, lots=0.001, price=49000.0,
        ))
        executor._request_processor.flush_outbox()
        executor._request_processor.drain_inbox()

        pending = executor._active_limit_orders[0]
        old_ref = pending.broker_ref
        pending.broker_ref = 'POST-MODIFY-REF'

        # Stale FILLED response against old_ref must NOT remove the order
        stale = QueryResponse(
            order_id=pending.pending_order_id,
            broker_response=BrokerResponse(
                broker_ref=old_ref, status=BrokerOrderStatus.FILLED,
                fill_price=49000.0, filled_lots=0.001,
                timestamp=datetime.now(timezone.utc),
            ),
        )
        executor._handle_query_response(stale)

        assert pending in executor._active_limit_orders
        assert len(executor.get_open_positions()) == 0


# =============================================================================
# 5. Partial-fill preserved behavior (until #326 cumulative drain lands)
# =============================================================================


class TestPartialFillPreservedBehavior:
    """PARTIALLY_FILLED keeps the order in the active list; in_flight cleared."""

    def test_partially_filled_keeps_polling(self):
        """PARTIALLY_FILLED → no state mutation, order stays active, in_flight cleared."""
        executor = _build_executor_with_poll_interval(MockExecutionMode.TIMEOUT, poll_interval_ms=5000)

        executor.on_tick(TickData(
            timestamp=datetime.now(timezone.utc),
            symbol='BTCUSD', bid=49999.0, ask=50001.0,
        ))
        executor.open_order(OpenOrderRequest(
            symbol='BTCUSD', order_type=OrderType.LIMIT,
            direction=OrderDirection.LONG, lots=0.001, price=49000.0,
        ))
        executor._request_processor.flush_outbox()
        executor._request_processor.drain_inbox()

        pending = executor._active_limit_orders[0]
        pending.in_flight_query = True

        partial = QueryResponse(
            order_id=pending.pending_order_id,
            broker_response=BrokerResponse(
                broker_ref=pending.broker_ref, status=BrokerOrderStatus.PARTIALLY_FILLED,
                fill_price=49000.0, filled_lots=0.0005,
                timestamp=datetime.now(timezone.utc),
            ),
        )
        executor._handle_query_response(partial)

        assert pending in executor._active_limit_orders
        assert pending.in_flight_query is False
        assert len(executor.get_open_positions()) == 0
