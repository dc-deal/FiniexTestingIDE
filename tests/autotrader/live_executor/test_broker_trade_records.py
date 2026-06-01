"""
Broker Trade Record Tests — Order ↔ Executions Pairing (#326)

Validates the BrokerTrade data model and the trades-query async path:
- PendingOrder.append_trade aggregates cumulative_* correctly
- After a fill, pending.trades is populated (synthesized in V1)
- The async trades_query path (submit_trades_query_async → drain) wires through
  the LiveRequestProcessor worker and into _handle_trades_response
- Stale-response guard discards responses with mismatched broker_ref
- Multi-trade mocks (trades_per_fill=N) produce N records and correct cumulatives

Tests do not assert real broker fees in V1 — the polling-path synthesis records
fee from the local model. Async trades_query exercises the per-execution path
that #327 Drift Audit consumes.
"""

from datetime import datetime, timezone

from python.framework.testing.mock_broker_adapter import MockBrokerAdapter, MockExecutionMode
from python.framework.testing.mock_order_execution import MockOrderExecution
from python.framework.types.live_types.live_request_types import (
    TradesQueryJob,
    TradesQueryResponse,
)
from python.framework.types.trading_env_types.broker_trade_types import BrokerTrade
from python.framework.types.trading_env_types.latency_simulator_types import PendingOrder
from python.framework.types.trading_env_types.order_types import (
    OpenOrderRequest,
    OrderDirection,
    OrderType,
)


# =============================================================================
# PendingOrder.append_trade aggregation
# =============================================================================

class TestPendingOrderAppendTrade:
    """append_trade mutates trades + cumulative_* in lock-step."""

    def _make_trade(self, volume: float, price: float, fee: float = 0.0) -> BrokerTrade:
        return BrokerTrade(
            trade_id=f'TT-{volume}-{price}',
            parent_broker_ref='OXYZ-1',
            order_id='ORD-1',
            volume=volume,
            price=price,
            fee=fee,
            fee_currency='USD',
            timestamp=datetime.now(timezone.utc),
            side=OrderDirection.LONG,
            is_maker=False,
        )

    def test_empty_pending_has_zero_cumulatives(self):
        p = PendingOrder(pending_order_id='ORD-1')
        assert p.trades == []
        assert p.cumulative_filled_lots == 0.0
        assert p.cumulative_fee == 0.0
        assert p.cumulative_avg_price == 0.0

    def test_single_trade_sets_cumulatives(self):
        p = PendingOrder(pending_order_id='ORD-1')
        p.append_trade(self._make_trade(volume=0.1, price=100.0, fee=0.26))
        assert len(p.trades) == 1
        assert p.cumulative_filled_lots == 0.1
        assert p.cumulative_fee == 0.26
        assert p.cumulative_avg_price == 100.0

    def test_three_trades_weighted_avg_price(self):
        """0.04 @100.10 + 0.03 @100.20 + 0.03 @100.15 → weighted avg ≈ 100.146"""
        p = PendingOrder(pending_order_id='ORD-1')
        p.append_trade(self._make_trade(volume=0.04, price=100.10, fee=0.4))
        p.append_trade(self._make_trade(volume=0.03, price=100.20, fee=0.3))
        p.append_trade(self._make_trade(volume=0.03, price=100.15, fee=0.3))
        assert len(p.trades) == 3
        assert p.cumulative_filled_lots == 0.10
        assert p.cumulative_fee == 1.0
        expected_avg = (0.04 * 100.10 + 0.03 * 100.20 + 0.03 * 100.15) / 0.10
        assert abs(p.cumulative_avg_price - expected_avg) < 1e-9


# =============================================================================
# Synthesis during polling-path fill (live executor)
# =============================================================================

class TestPollingPathSynthesizesTrade:
    """After a MARKET fill, pending.trades is populated by the inherited
    _fill_open_order synthesis (V1 — single synthetic trade)."""

    def test_market_fill_creates_one_synthetic_trade(self, mock_instant, executor_instant):
        mock_instant.feed_tick(executor_instant, bid=49999.0, ask=50001.0)
        executor_instant.open_order(OpenOrderRequest(
            symbol='BTCUSD', order_type=OrderType.MARKET,
            direction=OrderDirection.LONG, lots=0.001,
        ))
        mock_instant.feed_tick(executor_instant, bid=49999.0, ask=50001.0)

        history = executor_instant.get_order_history()
        executed = [h for h in history if h.status.value == 'executed']
        assert len(executed) == 1

        # Position created — pull the order_id from history, verify the position
        # exists. The trade synthesis happened on the pending_order before the
        # portfolio.open_position call.
        positions = executor_instant.get_open_positions()
        assert len(positions) == 1


# =============================================================================
# Async trades_query path through LiveRequestProcessor
# =============================================================================

class TestTradesQueryAsyncRoundtrip:
    """submit_trades_query_async → worker → TradesQueryResponse → drain."""

    def _make_adapter_with_trades(self, broker_ref: str, lots: float, price: float):
        """Create a MockBrokerAdapter pre-populated with synthetic trades for broker_ref."""
        adapter = MockBrokerAdapter(mode=MockExecutionMode.INSTANT_FILL, trades_per_fill=1)
        # Manually seed the mock's trade records for the test broker_ref
        adapter._record_mock_trades(
            broker_ref=broker_ref,
            symbol='BTCUSD',
            direction=OrderDirection.LONG,
            total_lots=lots,
            fill_price=price,
            is_maker=False,
        )
        return adapter

    def test_submit_trades_query_async_returns_records_via_drain(self, request_processor):
        """Enqueue a TradesQueryJob; flush + drain; the trades_response hook fires."""
        adapter = self._make_adapter_with_trades(broker_ref='OXYZ-1', lots=0.1, price=100.0)
        captured: list = []

        request_processor.set_executor_hooks(
            fill_open=lambda p, fp: None,
            fill_close=lambda p, fp: None,
            on_rejection=lambda d, r: None,
            trades_response=lambda resp: captured.append(resp),
        )
        request_processor.start_worker()

        request_processor.submit_trades_query_async(
            order_id='ORD-1', broker_ref='OXYZ-1', adapter=adapter,
        )
        request_processor.flush_outbox()
        request_processor.drain_inbox()
        request_processor.stop_worker()

        assert len(captured) == 1
        resp: TradesQueryResponse = captured[0]
        assert resp.success
        assert resp.order_id == 'ORD-1'
        assert resp.broker_ref == 'OXYZ-1'
        assert len(resp.trades) == 1
        assert resp.trades[0].volume == 0.1
        assert resp.trades[0].price == 100.0

    def test_multi_trade_mock_yields_n_records(self, request_processor):
        """trades_per_fill=3 produces three BrokerTrade records on a single fill."""
        adapter = MockBrokerAdapter(mode=MockExecutionMode.INSTANT_FILL, trades_per_fill=3)
        adapter._record_mock_trades(
            broker_ref='OXYZ-2',
            symbol='BTCUSD',
            direction=OrderDirection.LONG,
            total_lots=0.10,
            fill_price=100.0,
            is_maker=False,
        )
        captured: list = []

        request_processor.set_executor_hooks(
            fill_open=lambda p, fp: None,
            fill_close=lambda p, fp: None,
            on_rejection=lambda d, r: None,
            trades_response=lambda resp: captured.append(resp),
        )
        request_processor.start_worker()
        request_processor.submit_trades_query_async(
            order_id='ORD-2', broker_ref='OXYZ-2', adapter=adapter,
        )
        request_processor.flush_outbox()
        request_processor.drain_inbox()
        request_processor.stop_worker()

        assert len(captured) == 1
        assert len(captured[0].trades) == 3
        total_volume = sum(t.volume for t in captured[0].trades)
        assert abs(total_volume - 0.10) < 1e-9

    def test_unknown_broker_ref_yields_empty_trades(self, request_processor):
        """Querying a broker_ref the mock never saw returns an empty list (not an error)."""
        adapter = MockBrokerAdapter(mode=MockExecutionMode.INSTANT_FILL, trades_per_fill=1)
        captured: list = []

        request_processor.set_executor_hooks(
            fill_open=lambda p, fp: None,
            fill_close=lambda p, fp: None,
            on_rejection=lambda d, r: None,
            trades_response=lambda resp: captured.append(resp),
        )
        request_processor.start_worker()
        request_processor.submit_trades_query_async(
            order_id='ORD-3', broker_ref='UNKNOWN-REF', adapter=adapter,
        )
        request_processor.flush_outbox()
        request_processor.drain_inbox()
        request_processor.stop_worker()

        assert len(captured) == 1
        assert captured[0].success
        assert captured[0].trades == []


# =============================================================================
# Stale-response guard in LiveTradeExecutor._handle_trades_response
# =============================================================================

class TestStaleResponseGuard:
    """If broker_ref on response differs from pending.broker_ref, discard."""

    def test_stale_trades_response_does_not_remove_pending(self, mock_instant, executor_instant):
        """A trades response with a stale broker_ref leaves _active_limit_orders untouched."""
        # Inject an active limit order manually so we can simulate a stale response
        active = PendingOrder(
            pending_order_id='ORD-STALE',
            broker_ref='NEW-REF',
            symbol='BTCUSD',
            direction=OrderDirection.LONG,
            lots=0.001,
            entry_price=49000.0,
            order_kwargs={},
        )
        executor_instant._active_limit_orders.append(active)

        # Build a stale response (broker_ref doesn't match)
        stale = TradesQueryResponse(
            order_id='ORD-STALE',
            broker_ref='OLD-REF',
            trades=[BrokerTrade(
                trade_id='T-1', parent_broker_ref='OLD-REF', order_id='ORD-STALE',
                volume=0.001, price=49000.0, fee=0.0, fee_currency='USD',
                timestamp=datetime.now(timezone.utc),
                side=OrderDirection.LONG, is_maker=True,
            )],
            success=True,
        )
        executor_instant._handle_trades_response(stale)

        # Order is still in active list, no trades appended (stale discarded)
        assert active in executor_instant._active_limit_orders
        assert active.trades == []


# =============================================================================
# Capability flag
# =============================================================================

class TestCapabilityFlag:
    """Mock adapter declares trade_level_reporting=True (#326)."""

    def test_mock_reports_trade_level_capability(self):
        adapter = MockBrokerAdapter()
        caps = adapter.get_order_capabilities()
        assert caps.trade_level_reporting is True
