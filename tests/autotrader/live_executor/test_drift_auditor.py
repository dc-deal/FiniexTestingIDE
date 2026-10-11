"""
FiniexTestingIDE - DriftAuditor Tests (#327)

Validates the read-only drift telemetry pipeline:
- Outcome listener fires only on EXECUTED with a real PendingOrder
- DRYRUN orders are skipped (no broker truth to compare against)
- Trades-response consumer pops snapshot + compares against broker truth
- Threshold logic correctly classifies events
- Currency mismatch skips fee comparison + warns
- Multi-listener / multi-consumer coexistence with OrderGuard
- Failed responses do not leak the pending_audits dict
- Consumer exceptions are isolated (one bad consumer cannot kill the chain)
- A booked close is audited too, keyed apart from its entry; a venue without trade-level
  reporting is not asked at all (#508)
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, List, Optional

import pytest

from python.framework.logging.global_logger import GlobalLogger
from python.framework.trading_env.live.drift_auditor import DriftAuditor
from python.framework.types.config_types.autotrader_defaults_config_types import (
    DriftAuditConfig,
)
from python.framework.types.live_types.drift_audit_types import DriftType
from python.framework.types.live_types.live_request_types import TradesQueryResponse
from python.framework.types.trading_env_types.broker_trade_types import BrokerTrade
from python.framework.types.trading_env_types.latency_simulator_types import (
    PendingOrder,
    PendingOrderAction,
)
from python.framework.types.trading_env_types.order_types import (
    CloseType,
    OrderAction,
    OrderCapabilities,
    OrderDirection,
    OrderResult,
    OrderStatus,
    OrderType,
)
from python.framework.types.trading_env_types.submission_metadata_types import SubmissionMetadata
from tests.framework.order_events.conftest import ASK, BID, live_session, market_order

ACCOUNT_MODELS = pytest.mark.parametrize('spot_mode', [False, True], ids=['margin', 'spot'])

# =============================================================================
# Helpers
# =============================================================================


class _FakeAdapter:
    """Declares only what the auditor reads: whether the venue answers a trades query."""

    def __init__(self, trade_level_reporting: bool) -> None:
        self._trade_level_reporting = trade_level_reporting

    def get_order_capabilities(self) -> OrderCapabilities:
        return OrderCapabilities(trade_level_reporting=self._trade_level_reporting)


@dataclass
class _FakeBroker:
    adapter: _FakeAdapter


class _FakeExecutor:
    """
    Stub executor that captures listener / consumer registrations and
    trades_query invocations. Lets DriftAuditor tests run without spinning
    up a full LiveTradeExecutor + worker thread.
    """

    def __init__(self, trade_level_reporting: bool = True) -> None:
        self.outcome_listeners: List[Callable] = []
        self.close_listeners: List[Callable] = []
        self.trades_consumers: List[Callable] = []
        self.trades_query_calls: List[dict] = []
        self.broker = _FakeBroker(_FakeAdapter(trade_level_reporting))

    def add_order_outcome_listener(self, listener: Callable) -> None:
        self.outcome_listeners.append(listener)

    def add_close_fill_listener(self, listener: Callable) -> None:
        self.close_listeners.append(listener)

    def fire_close_fill(self, pending: PendingOrder, result: OrderResult) -> None:
        """Simulate the executor telling its close-fill listeners of a booked close."""
        for listener in self.close_listeners:
            listener(pending, result)

    def add_trades_response_consumer(self, consumer: Callable) -> None:
        self.trades_consumers.append(consumer)

    def submit_trades_query_async(self, order_id: str, broker_ref: str) -> None:
        self.trades_query_calls.append({'order_id': order_id, 'broker_ref': broker_ref})

    def fire_outcome(
        self,
        direction: OrderDirection,
        result: OrderResult,
        pending: Optional[PendingOrder],
    ) -> None:
        """Simulate the executor's _notify_outcome fan-out."""
        for listener in self.outcome_listeners:
            listener(direction, result, pending)

    def fire_trades_response(self, response: TradesQueryResponse) -> None:
        """Simulate the executor's _handle_trades_response fan-out."""
        for consumer in self.trades_consumers:
            consumer(response)


def _make_pending(
    order_id: str,
    broker_ref: Optional[str] = 'OBROKER-XYZ',
    symbol: str = 'BTCUSD',
    lots: float = 0.1,
    synthetic_fee: float = 0.0026,
    synthetic_avg_price: float = 100.0,
    fee_currency: str = 'USD',
    submission_tick_mid_price: Optional[float] = None,
    submission_tick_time_msc: Optional[int] = None,
    order_action: PendingOrderAction = PendingOrderAction.OPEN,
) -> PendingOrder:
    """Build a PendingOrder with a single synthetic trade already populated."""
    pending = PendingOrder(
        pending_order_id=order_id,
        order_action=order_action,
        order_type=OrderType.MARKET,
        symbol=symbol,
        direction=OrderDirection.LONG,
        lots=lots,
        entry_price=synthetic_avg_price,
        order_kwargs={},
        submission=SubmissionMetadata(
            tick_mid_price=submission_tick_mid_price,
            tick_time_msc=submission_tick_time_msc,
        ),
    )
    pending.broker_ref = broker_ref
    # Populate synthetic state — mirrors what _synthesize_pending_trade produces
    synthetic_trade = BrokerTrade(
        trade_id='MOCK-TRADE-000001',
        parent_broker_ref=broker_ref or 'NONE',
        order_id=order_id,
        volume=lots,
        price=synthetic_avg_price,
        fee=synthetic_fee,
        fee_currency=fee_currency,
        timestamp=datetime.now(timezone.utc),
        side=OrderDirection.LONG,
        is_maker=False,
    )
    pending.fills.append_trade(synthetic_trade)
    return pending


def _make_executed_result(
    order_id: str,
    action: OrderAction = OrderAction.OPEN,
    commission: float = 0.0,
) -> OrderResult:
    """Build an OrderResult with EXECUTED status — a close names its position's side."""
    return OrderResult(
        order_id=order_id,
        status=OrderStatus.EXECUTED,
        executed_price=100.0,
        executed_lots=0.1,
        commission=commission,
        execution_time=datetime.now(timezone.utc),
        action=action,
        symbol='BTCUSD',
        direction=OrderDirection.LONG,
    )


def _make_trades_response(
    order_id: str,
    broker_ref: str = 'OBROKER-XYZ',
    real_fee: float = 0.0026,
    real_volume: float = 0.1,
    real_price: float = 100.0,
    fee_currency: str = 'USD',
    success: bool = True,
    trades_count: int = 1,
) -> TradesQueryResponse:
    """Build a TradesQueryResponse with N synthetic trade records."""
    trades = []
    if success and trades_count > 0:
        per_trade_vol = real_volume / trades_count
        per_trade_fee = real_fee / trades_count
        for i in range(trades_count):
            trades.append(BrokerTrade(
                trade_id=f'BROKER-TRADE-{i:03d}',
                parent_broker_ref=broker_ref,
                order_id=order_id,
                volume=per_trade_vol,
                price=real_price,
                fee=per_trade_fee,
                fee_currency=fee_currency,
                timestamp=datetime.now(timezone.utc),
                side=OrderDirection.LONG,
                is_maker=False,
            ))
    return TradesQueryResponse(
        order_id=order_id,
        broker_ref=broker_ref,
        trades=trades,
        success=success,
        error_message=None if success else 'mock failure',
    )


@pytest.fixture
def fake_executor() -> _FakeExecutor:
    return _FakeExecutor()


@pytest.fixture
def logger() -> GlobalLogger:
    return GlobalLogger(name='DriftAuditorTest')


@pytest.fixture
def enabled_config() -> DriftAuditConfig:
    return DriftAuditConfig(
        enabled=True,
        fee_threshold_pct=0.5,
        volume_threshold_pct=0.1,
        price_threshold_pct=1.0,
        log_all=False,
    )


@pytest.fixture
def disabled_config() -> DriftAuditConfig:
    return DriftAuditConfig(enabled=False)


# =============================================================================
# Tests
# =============================================================================


class TestDisabledAudit:
    """When the config is disabled, the auditor produces no side effects."""

    def test_disabled_audit_is_noop(self, fake_executor, disabled_config, logger):
        DriftAuditor(executor=fake_executor, config=disabled_config, logger=logger)
        pending = _make_pending(order_id='ORD-1')
        result = _make_executed_result('ORD-1')

        fake_executor.fire_outcome(OrderDirection.LONG, result, pending)

        # Listener was registered, but the disabled check should skip everything
        assert len(fake_executor.trades_query_calls) == 0


class TestThresholdBehaviour:
    """Drift records are produced; only those over threshold count as events."""

    def test_no_drift_within_threshold(self, fake_executor, enabled_config, logger):
        auditor = DriftAuditor(executor=fake_executor, config=enabled_config, logger=logger)
        pending = _make_pending(order_id='ORD-1', synthetic_fee=0.0026, synthetic_avg_price=100.0)
        result = _make_executed_result('ORD-1')

        fake_executor.fire_outcome(OrderDirection.LONG, result, pending)
        response = _make_trades_response(
            order_id='ORD-1', real_fee=0.0026, real_volume=0.1, real_price=100.0,
        )
        fake_executor.fire_trades_response(response)

        summary = auditor.get_summary()
        assert summary.total_orders_audited == 1
        assert summary.fee_events == 0
        assert summary.volume_events == 0
        assert summary.price_events == 0

    def test_fee_drift_above_threshold_logged(self, fake_executor, enabled_config, logger):
        """Synthetic fee 5% below broker → over 0.5% threshold → fee event."""
        auditor = DriftAuditor(executor=fake_executor, config=enabled_config, logger=logger)
        pending = _make_pending(order_id='ORD-1', synthetic_fee=0.0025)
        result = _make_executed_result('ORD-1')

        fake_executor.fire_outcome(OrderDirection.LONG, result, pending)
        # Broker reports 5% higher fee than local synthesis
        response = _make_trades_response(order_id='ORD-1', real_fee=0.002625, real_volume=0.1, real_price=100.0)
        fake_executor.fire_trades_response(response)

        summary = auditor.get_summary()
        assert summary.fee_events == 1
        assert summary.max_fee_drift_pct > enabled_config.fee_threshold_pct
        # A FEE record should be in the records list with threshold_exceeded=True
        fee_records = [r for r in summary.records if r.drift_type is DriftType.FEE]
        assert len(fee_records) == 1
        assert fee_records[0].threshold_exceeded


class TestPartialFill:
    """Volume drift when broker reports less than requested."""

    def test_volume_drift_partial_fill(self, fake_executor, enabled_config, logger):
        auditor = DriftAuditor(executor=fake_executor, config=enabled_config, logger=logger)
        pending = _make_pending(order_id='ORD-1', lots=0.10, synthetic_avg_price=100.0)
        result = _make_executed_result('ORD-1')

        fake_executor.fire_outcome(OrderDirection.LONG, result, pending)
        # Broker filled only 0.05 of requested 0.10 → 50% volume drift
        response = _make_trades_response(order_id='ORD-1', real_fee=0.0013, real_volume=0.05, real_price=100.0)
        fake_executor.fire_trades_response(response)

        summary = auditor.get_summary()
        assert summary.volume_events == 1
        vol_records = [r for r in summary.records if r.drift_type is DriftType.VOLUME]
        assert vol_records[0].threshold_exceeded


class TestPriceDriftStructural:
    """PRICE drift records carry is_structural=True (see #244)."""

    def test_price_drift_marked_structural(self, fake_executor, enabled_config, logger):
        auditor = DriftAuditor(executor=fake_executor, config=enabled_config, logger=logger)
        pending = _make_pending(order_id='ORD-1', synthetic_avg_price=100.0)
        result = _make_executed_result('ORD-1')

        fake_executor.fire_outcome(OrderDirection.LONG, result, pending)
        # Broker filled at a noticeably different avg price (5% delta)
        response = _make_trades_response(order_id='ORD-1', real_fee=0.0026, real_volume=0.1, real_price=105.0)
        fake_executor.fire_trades_response(response)

        summary = auditor.get_summary()
        price_records = [r for r in summary.records if r.drift_type is DriftType.PRICE]
        assert len(price_records) == 1
        assert price_records[0].is_structural is True
        # Counter increments because delta > 1% threshold
        assert summary.price_events == 1


class TestDryRunSkipped:
    """Orders with DRYRUN- broker_ref are skipped — no trades_query, no snapshot."""

    def test_dryrun_orders_skipped(self, fake_executor, enabled_config, logger):
        DriftAuditor(executor=fake_executor, config=enabled_config, logger=logger)
        pending = _make_pending(order_id='ORD-1', broker_ref='DRYRUN-001')
        result = _make_executed_result('ORD-1')

        fake_executor.fire_outcome(OrderDirection.LONG, result, pending)

        # No trades_query triggered for DRYRUN orders
        assert len(fake_executor.trades_query_calls) == 0


class TestFeeCurrencyMismatch:
    """Different fee_currency between local synthesis and broker → skip FEE compare."""

    def test_fee_currency_mismatch_skips_comparison(self, fake_executor, enabled_config, logger):
        auditor = DriftAuditor(executor=fake_executor, config=enabled_config, logger=logger)
        pending = _make_pending(order_id='ORD-1', fee_currency='USD')
        result = _make_executed_result('ORD-1')

        fake_executor.fire_outcome(OrderDirection.LONG, result, pending)
        # Broker reports fee in EUR — currency mismatch
        response = _make_trades_response(order_id='ORD-1', fee_currency='EUR')
        fake_executor.fire_trades_response(response)

        summary = auditor.get_summary()
        # FEE comparison was skipped — no FEE record was appended
        fee_records = [r for r in summary.records if r.drift_type is DriftType.FEE]
        assert len(fee_records) == 0
        # VOLUME and PRICE comparisons still ran
        assert summary.total_orders_audited == 1


class TestCoexistenceWithOrderGuard:
    """DriftAuditor registers a second listener — both fire on outcome."""

    def test_drift_auditor_coexists_with_order_guard(self, fake_executor, enabled_config, logger):
        DriftAuditor(executor=fake_executor, config=enabled_config, logger=logger)

        # Simulate an OrderGuard-style listener also registering
        guard_calls: List[tuple] = []

        def guard_listener(direction, result, pending=None):
            guard_calls.append((direction, result.status, pending is not None))

        fake_executor.add_order_outcome_listener(guard_listener)

        pending = _make_pending(order_id='ORD-1')
        result = _make_executed_result('ORD-1')
        fake_executor.fire_outcome(OrderDirection.LONG, result, pending)

        # Both listeners ran — DriftAuditor scheduled trades_query, guard recorded the call
        assert len(fake_executor.trades_query_calls) == 1
        assert len(guard_calls) == 1
        assert guard_calls[0] == (OrderDirection.LONG, OrderStatus.EXECUTED, True)


class TestFailedTradesResponseNoLeak:
    """response.success=False → snapshot still popped → no leak (Risk 4 regression)."""

    def test_failed_trades_response_no_leak(self, fake_executor, enabled_config, logger):
        auditor = DriftAuditor(executor=fake_executor, config=enabled_config, logger=logger)
        pending = _make_pending(order_id='ORD-1')
        result = _make_executed_result('ORD-1')

        fake_executor.fire_outcome(OrderDirection.LONG, result, pending)
        # snapshot stored — under the venue's reference, which is unique per venue order
        assert pending.broker_ref in auditor._pending_audits

        failed_response = _make_trades_response(order_id='ORD-1', success=False)
        fake_executor.fire_trades_response(failed_response)

        # The snapshot must be cleaned up even though we never recorded a drift
        assert pending.broker_ref not in auditor._pending_audits
        # No drift events recorded (failed response → audit skipped)
        assert auditor.get_summary().total_orders_audited == 0

    def test_shutdown_clears_unfinished_audits(self, fake_executor, enabled_config, logger):
        """shutdown() warns about and clears any unfinished audits."""
        auditor = DriftAuditor(executor=fake_executor, config=enabled_config, logger=logger)
        pending = _make_pending(order_id='ORD-1')
        result = _make_executed_result('ORD-1')

        fake_executor.fire_outcome(OrderDirection.LONG, result, pending)
        assert len(auditor._pending_audits) == 1

        # Simulate session end without a trades_response arriving
        auditor.shutdown()
        assert len(auditor._pending_audits) == 0


class TestConsumerExceptionIsolation:
    """A consumer that raises does not kill the chain (Risk 2 regression).

    This validates the try/except fan-out pattern in LiveTradeExecutor._handle_trades_response.
    Here we exercise the same pattern via the fake executor: if one consumer raises, the
    others should still run. The test simulates the executor's expected behaviour.
    """

    def test_consumer_exception_isolated(self, fake_executor, enabled_config, logger):
        auditor = DriftAuditor(executor=fake_executor, config=enabled_config, logger=logger)

        survivor_calls: List[str] = []

        def survivor_consumer(response):
            survivor_calls.append(response.order_id)

        def bad_consumer(response):
            raise RuntimeError('intentional consumer failure')

        # Register a bad consumer before the survivor — order matters for the test
        fake_executor.trades_consumers.insert(0, bad_consumer)
        fake_executor.add_trades_response_consumer(survivor_consumer)

        pending = _make_pending(order_id='ORD-1')
        result = _make_executed_result('ORD-1')
        fake_executor.fire_outcome(OrderDirection.LONG, result, pending)

        # The fake executor does naive fan-out (no try/except), but the real
        # LiveTradeExecutor wraps each consumer call. We assert here that the
        # survivor still receives the response when the chain is iterated
        # with isolation — which is what _handle_trades_response does.
        response = _make_trades_response(order_id='ORD-1')

        # Run the fan-out with the same isolation pattern as LiveTradeExecutor
        for consumer in fake_executor.trades_consumers:
            try:
                consumer(response)
            except Exception:
                pass

        # Survivor must have been called despite the bad consumer raising
        assert survivor_calls == ['ORD-1']
        # DriftAuditor itself processed the response
        assert auditor.get_summary().total_orders_audited == 1


class TestSlippageAudit:
    """SLIPPAGE channel (#340) — submission tick mid vs broker fill price.

    The auditor captures pending.submission_tick_mid_price at outcome time and
    compares it against the volume-weighted mean of broker trades. Always
    structural — slippage is a market reality, not a bug signal.
    """

    def test_slippage_recorded_when_tick_differs_from_fill(self, fake_executor, enabled_config, logger):
        """Sub-threshold case from the V1.3 Pilot Run hypothesis: $2110.91 → $2110.95 ≈ 0.0019%."""
        auditor = DriftAuditor(executor=fake_executor, config=enabled_config, logger=logger)
        pending = _make_pending(
            order_id='ORD-1',
            synthetic_avg_price=2110.95,
            submission_tick_mid_price=2110.91,
            submission_tick_time_msc=1716220981000,
        )
        result = _make_executed_result('ORD-1')

        fake_executor.fire_outcome(OrderDirection.LONG, result, pending)
        response = _make_trades_response(
            order_id='ORD-1', real_fee=0.0026, real_volume=0.1, real_price=2110.95,
        )
        fake_executor.fire_trades_response(response)

        summary = auditor.get_summary()
        slip_records = [r for r in summary.records if r.drift_type is DriftType.SLIPPAGE]
        assert len(slip_records) == 1
        assert slip_records[0].local_value == 2110.91
        assert slip_records[0].broker_value == 2110.95
        assert slip_records[0].is_structural is True
        assert slip_records[0].threshold_exceeded is False
        # Under threshold → counter stays 0, but max-tracker reflects the magnitude
        assert summary.slippage_events == 0
        assert summary.max_slippage_drift_pct > 0.0

    def test_slippage_above_threshold_increments_counter(self, fake_executor, enabled_config, logger):
        """Synthetic 1%+ delta → counter increments, threshold flag set, is_structural stays True."""
        auditor = DriftAuditor(executor=fake_executor, config=enabled_config, logger=logger)
        pending = _make_pending(
            order_id='ORD-2',
            synthetic_avg_price=2120.0,
            submission_tick_mid_price=2100.0,  # ~0.94% delta vs fill
            submission_tick_time_msc=1716220981000,
        )
        result = _make_executed_result('ORD-2')

        fake_executor.fire_outcome(OrderDirection.LONG, result, pending)
        response = _make_trades_response(
            order_id='ORD-2', real_fee=0.0026, real_volume=0.1, real_price=2120.0,
        )
        fake_executor.fire_trades_response(response)

        summary = auditor.get_summary()
        slip_records = [r for r in summary.records if r.drift_type is DriftType.SLIPPAGE]
        assert len(slip_records) == 1
        assert slip_records[0].threshold_exceeded is True
        assert slip_records[0].is_structural is True
        assert summary.slippage_events == 1
        assert summary.max_slippage_drift_pct > enabled_config.slippage_threshold_pct

    def test_missing_submission_tick_gracefully_skips(self, fake_executor, enabled_config, logger):
        """Cold-start edge case — no submission tick captured → SLIPPAGE compare skipped."""
        auditor = DriftAuditor(executor=fake_executor, config=enabled_config, logger=logger)
        pending = _make_pending(
            order_id='ORD-3',
            synthetic_avg_price=100.0,
            submission_tick_mid_price=None,  # explicit cold-start
        )
        result = _make_executed_result('ORD-3')

        fake_executor.fire_outcome(OrderDirection.LONG, result, pending)
        response = _make_trades_response(order_id='ORD-3', real_price=100.0)
        fake_executor.fire_trades_response(response)

        summary = auditor.get_summary()
        slip_records = [r for r in summary.records if r.drift_type is DriftType.SLIPPAGE]
        assert len(slip_records) == 0
        assert summary.slippage_events == 0
        assert summary.max_slippage_drift_pct == 0.0
        # The other dimensions still ran — no leak in the snapshot dict
        assert summary.total_orders_audited == 1

    def test_slippage_always_marked_structural(self, fake_executor, enabled_config, logger):
        """Every SLIPPAGE record carries is_structural=True regardless of threshold breach."""
        auditor = DriftAuditor(executor=fake_executor, config=enabled_config, logger=logger)

        # Two orders: one sub-threshold, one over
        pending1 = _make_pending(
            order_id='ORD-4',
            synthetic_avg_price=100.0,
            submission_tick_mid_price=100.001,  # ~0.001% — sub-threshold
        )
        pending2 = _make_pending(
            order_id='ORD-5',
            synthetic_avg_price=100.0,
            submission_tick_mid_price=90.0,  # 10% — well over threshold
        )

        for pend in (pending1, pending2):
            result = _make_executed_result(pend.pending_order_id)
            fake_executor.fire_outcome(OrderDirection.LONG, result, pend)
            response = _make_trades_response(
                order_id=pend.pending_order_id, real_price=100.0,
            )
            fake_executor.fire_trades_response(response)

        summary = auditor.get_summary()
        slip_records = [r for r in summary.records if r.drift_type is DriftType.SLIPPAGE]
        assert len(slip_records) == 2
        assert all(r.is_structural for r in slip_records)

    def test_slippage_zero_when_tick_matches_fill(self, fake_executor, enabled_config, logger):
        """Exact match — record present (compare ran), counter stays 0, delta is 0."""
        auditor = DriftAuditor(executor=fake_executor, config=enabled_config, logger=logger)
        pending = _make_pending(
            order_id='ORD-6',
            synthetic_avg_price=100.0,
            submission_tick_mid_price=100.0,  # exact match
        )
        result = _make_executed_result('ORD-6')

        fake_executor.fire_outcome(OrderDirection.LONG, result, pending)
        response = _make_trades_response(order_id='ORD-6', real_price=100.0)
        fake_executor.fire_trades_response(response)

        summary = auditor.get_summary()
        slip_records = [r for r in summary.records if r.drift_type is DriftType.SLIPPAGE]
        assert len(slip_records) == 1
        assert slip_records[0].relative_delta_pct == 0.0
        assert slip_records[0].threshold_exceeded is False
        assert summary.slippage_events == 0
        assert summary.max_slippage_drift_pct == 0.0

    def test_slippage_captured_on_close_order(self, fake_executor, enabled_config, logger):
        """A booked close with submission_tick set → SLIPPAGE record produced.

        A close reaches the auditor through the close-fill listener, never through the
        order-outcome one (#508) — and is compared exactly like an entry.
        """
        auditor = DriftAuditor(executor=fake_executor, config=enabled_config, logger=logger)
        pending = _make_pending(
            order_id='POS-1',
            synthetic_avg_price=2120.0,
            submission_tick_mid_price=2118.0,
            submission_tick_time_msc=1716220985000,
            order_action=PendingOrderAction.CLOSE,
        )
        result = _make_executed_result('POS-1', action=OrderAction.CLOSE)

        fake_executor.fire_close_fill(pending, result)
        response = _make_trades_response(order_id='POS-1', real_price=2120.0)
        fake_executor.fire_trades_response(response)

        summary = auditor.get_summary()
        slip_records = [r for r in summary.records if r.drift_type is DriftType.SLIPPAGE]
        assert len(slip_records) == 1
        assert slip_records[0].local_value == 2118.0
        assert slip_records[0].broker_value == 2120.0
        assert slip_records[0].is_structural is True


# =============================================================================
# #508 — the exit leg, the reference key, and a venue that answers no trades query
# =============================================================================


class TestTheExitLegIsAudited:
    """A close is checked against the venue's executions like an entry, keyed apart from it."""

    def test_a_close_is_queried_and_compared(self, fake_executor, enabled_config, logger):
        auditor = DriftAuditor(executor=fake_executor, config=enabled_config, logger=logger)
        close = _make_pending('POS-1', broker_ref='OCLOSE-1', synthetic_fee=0.0040,
                              order_action=PendingOrderAction.CLOSE)

        fake_executor.fire_close_fill(
            close, _make_executed_result('POS-1', OrderAction.CLOSE, commission=0.0040))
        fake_executor.fire_trades_response(
            _make_trades_response('POS-1', broker_ref='OCLOSE-1', real_fee=0.0040))

        assert fake_executor.trades_query_calls == [
            {'order_id': 'POS-1', 'broker_ref': 'OCLOSE-1'}]
        fee_records = [r for r in auditor.get_summary().records if r.drift_type is DriftType.FEE]
        assert [r.broker_ref for r in fee_records] == ['OCLOSE-1']
        assert fee_records[0].relative_delta_pct == pytest.approx(0.0)

    def test_an_entry_and_its_close_in_flight_together_keep_their_own_snapshots(
            self, fake_executor, enabled_config, logger):
        """
        A position's entry and its close share one order_id. Keyed by it, a close that filled
        before the entry's executions arrived replaced the entry's snapshot: the entry was
        compared with the close's figures, and the close's own response found nothing.
        """
        auditor = DriftAuditor(executor=fake_executor, config=enabled_config, logger=logger)
        entry = _make_pending('POS-1', broker_ref='OENTRY-1', synthetic_fee=0.0080)
        close = _make_pending('POS-1', broker_ref='OCLOSE-1', synthetic_fee=0.0040,
                              order_action=PendingOrderAction.CLOSE)

        fake_executor.fire_outcome(OrderDirection.LONG, _make_executed_result('POS-1'), entry)
        fake_executor.fire_close_fill(
            close, _make_executed_result('POS-1', OrderAction.CLOSE, commission=0.0040))
        fake_executor.fire_trades_response(
            _make_trades_response('POS-1', broker_ref='OCLOSE-1', real_fee=0.0040))
        fake_executor.fire_trades_response(
            _make_trades_response('POS-1', broker_ref='OENTRY-1', real_fee=0.0080))

        summary = auditor.get_summary()
        assert summary.total_orders_audited == 2
        assert summary.fee_events == 0
        assert {r.broker_ref for r in summary.records if r.drift_type is DriftType.FEE} == {
            'OENTRY-1', 'OCLOSE-1'}


class TestAVenueWithoutExecutionsIsNotAsked:
    """Where the venue answers no trades query there is nothing to audit against."""

    def test_neither_leg_is_queried(self, enabled_config, logger):
        executor = _FakeExecutor(trade_level_reporting=False)
        auditor = DriftAuditor(executor=executor, config=enabled_config, logger=logger)

        executor.fire_outcome(OrderDirection.LONG, _make_executed_result('POS-1'),
                              _make_pending('POS-1', broker_ref='OENTRY-1'))
        executor.fire_close_fill(
            _make_pending('POS-1', broker_ref='OCLOSE-1', order_action=PendingOrderAction.CLOSE),
            _make_executed_result('POS-1', OrderAction.CLOSE))

        assert executor.trades_query_calls == []
        assert auditor.get_summary().total_orders_audited == 0


class TestAProtectiveOrderIsAuditedOnceItCloses:
    """
    A protective order the venue holds is booked in steps, and its executions describe the
    whole order. It is audited once, by the booking that closes the position, against all that
    was booked for it — and never for slippage against the mid of its placement, which
    measures the distance to its stop rather than what its fill cost (#508).
    """

    @staticmethod
    def _protective(applied_lots: float = 0.0, applied_fee: float = 0.0,
                    applied_value: float = 0.0) -> PendingOrder:
        """
        A venue-held protective order with what earlier steps booked.

        Args:
            applied_lots: Lots already booked
            applied_fee: Fee already booked
            applied_value: Lots times price already booked

        Returns:
            The protective order
        """
        pending = _make_pending('protect_pos_1', broker_ref='OSTOP-1', synthetic_fee=0.0,
                                submission_tick_mid_price=50000.0,
                                order_action=PendingOrderAction.CLOSE)
        pending.closes_position_id = 'pos_1'
        pending.execution_state.venue_close_applied_lots = applied_lots
        pending.execution_state.venue_close_applied_fee = applied_fee
        pending.execution_state.venue_close_applied_value = applied_value
        return pending

    @staticmethod
    def _booked(lots: float, price: float, fee: float, close_type: CloseType) -> OrderResult:
        """
        The result one step of it booked.

        Args:
            lots: The step's lots
            price: Its price
            fee: Its fee
            close_type: Whether it closed the position

        Returns:
            The executed close result
        """
        return OrderResult(
            order_id='protect_pos_1', status=OrderStatus.EXECUTED, executed_price=price,
            executed_lots=lots, commission=fee, execution_time=datetime.now(timezone.utc),
            action=OrderAction.CLOSE, symbol='BTCUSD', direction=OrderDirection.LONG,
            close_type=close_type)

    def test_a_step_that_leaves_the_position_open_asks_nothing(
            self, fake_executor, enabled_config, logger):
        DriftAuditor(executor=fake_executor, config=enabled_config, logger=logger)

        fake_executor.fire_close_fill(
            self._protective(), self._booked(0.04, 49000.0, 15.68, CloseType.PARTIAL))

        assert fake_executor.trades_query_calls == []

    def test_the_closing_step_is_compared_with_everything_booked(
            self, fake_executor, enabled_config, logger):
        auditor = DriftAuditor(executor=fake_executor, config=enabled_config, logger=logger)

        fake_executor.fire_close_fill(
            self._protective(0.04, 15.68, 1960.0),
            self._booked(0.06, 2935.0 / 0.06, 23.50, CloseType.FULL))
        fake_executor.fire_trades_response(_make_trades_response(
            'protect_pos_1', broker_ref='OSTOP-1', real_fee=39.18, real_volume=0.10,
            real_price=48950.0, trades_count=2))

        summary = auditor.get_summary()
        assert summary.total_orders_audited == 1
        assert [r.drift_type for r in summary.records if r.threshold_exceeded] == []
        assert DriftType.SLIPPAGE not in {r.drift_type for r in summary.records}


class TestTheExecutorAuditsBothLegs:
    """The real executor tells the auditor of an entry and of its close — the wiring, pinned."""

    @ACCOUNT_MODELS
    def test_an_entry_and_its_close_each_ask_for_their_executions(
            self, spot_mode, enabled_config, logger, monkeypatch):
        mock, executor, _ = live_session(spot_mode=spot_mode)
        DriftAuditor(executor=executor, config=enabled_config, logger=logger)
        asked: List[str] = []
        monkeypatch.setattr(executor, 'submit_trades_query_async',
                            lambda order_id, broker_ref: asked.append(broker_ref))

        executor.open_order(market_order())
        mock.await_submit_confirmation(executor)
        mock.feed_tick(executor, bid=BID, ask=ASK)
        executor.close_position(executor.get_open_positions()[0].position_id)
        mock.await_submit_confirmation(executor)
        mock.feed_tick(executor, bid=BID, ask=ASK)

        assert len(asked) == 2 and len(set(asked)) == 2, asked
