"""
Sim/Live Parity — Modify and Cancel Algo-Facing Contract (#318)

The async-parity rule says: an operation that is async in live MUST have a
parallel pending-resolution path in sim. The two pipelines use different
mechanisms (msc-clock for sim, worker-thread for live), but the algo-facing
contract must be identical.

This file directly invokes modify/cancel on both pipelines and asserts the
return shape, in_flight_operation transitions, and post-resolve state are
identical. Lightweight by design — does NOT run a full tick loop; instead
spins up minimal sim and live executors and drives them deterministically.

The full-bar-parity tests in test_bar_parity_kraken_spot_*.py cover bar
and trade-record parity; this file covers the modify/cancel API contract.
"""

from datetime import datetime, timezone

import pytest

from python.framework.logging.global_logger import GlobalLogger
from python.framework.testing.mock_adapter import MockBrokerAdapter, MockExecutionMode
from python.framework.testing.mock_order_execution import MockOrderExecution
from python.framework.trading_env.broker_config import BrokerConfig
from python.framework.trading_env.live.live_trade_executor import LiveTradeExecutor
from python.framework.trading_env.simulation.trade_simulator import TradeSimulator
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.trading_env_types.broker_types import BrokerType
from python.framework.types.trading_env_types.latency_simulator_types import PendingOperation
from python.framework.types.trading_env_types.order_types import (
    ModificationRejectionReason,
    ModificationStatus,
    OpenOrderRequest,
    OrderDirection,
    OrderType,
)


# =============================================================================
# Pipeline-specific setup helpers
# =============================================================================

def _build_sim_executor() -> TradeSimulator:
    """Sim executor with zero-latency INSTANT_FILL Mock."""
    adapter = MockBrokerAdapter(mode=MockExecutionMode.INSTANT_FILL)
    broker_config = BrokerConfig(BrokerType.KRAKEN_SPOT, adapter)
    return TradeSimulator(
        broker_config=broker_config,
        initial_balance=10000.0,
        account_currency='USD',
        logger=GlobalLogger('ParitySim'),
        seeds={'inbound_latency_seed': 42},
        inbound_latency_min_ms=0,
        inbound_latency_max_ms=0,
    )


def _feed_sim_tick(executor: TradeSimulator, msc: int) -> TickData:
    """Direct tick feed for sim — controls msc explicitly."""
    tick = TickData(
        timestamp=datetime.fromtimestamp(msc / 1000.0, tz=timezone.utc),
        symbol='BTCUSD', bid=49999.0, ask=50001.0,
        collected_msc=msc, time_msc=msc,
    )
    executor.on_tick(tick)
    return tick


def _setup_sim_active_limit(sim) -> str:
    """Submit LIMIT and drive sim to land it in _active_limit_orders."""
    _feed_sim_tick(sim, msc=1000)
    result = sim.open_order(OpenOrderRequest(
        symbol='BTCUSD', order_type=OrderType.LIMIT,
        direction=OrderDirection.LONG, lots=0.001, price=49000.0,
    ))
    _feed_sim_tick(sim, msc=1001)  # drain latency → _active_limit_orders
    return result.order_id


def _setup_live_active_limit(mock_exec: MockOrderExecution, live: LiveTradeExecutor) -> str:
    """Submit LIMIT and drive live to land it in _active_limit_orders with broker_ref confirmed."""
    mock_exec.feed_tick(live, bid=49999.0, ask=50001.0)
    result = live.open_order(OpenOrderRequest(
        symbol='BTCUSD', order_type=OrderType.LIMIT,
        direction=OrderDirection.LONG, lots=0.001, price=49000.0,
    ))
    mock_exec.await_submit_confirmation(live)  # drain → broker_ref set
    return result.order_id


def _resolve_sim_pending(sim: TradeSimulator, msc: int = 2000) -> None:
    """Drive sim through Phase 0 to resolve a pending modify/cancel."""
    _feed_sim_tick(sim, msc=msc)


def _resolve_live_pending(mock_exec: MockOrderExecution, live: LiveTradeExecutor) -> None:
    """Drive live drain_inbox (no Phase-2 polling) to resolve a pending modify/cancel."""
    mock_exec.await_submit_confirmation(live)


# =============================================================================
# Modify parity tests
# =============================================================================

class TestModifyContractParity:
    """modify_limit_order returns identical ModificationResult shape in sim and live."""

    def test_modify_accept_returns_pending_in_both(self):
        """Valid modify on active LIMIT → success=True, status=PENDING in both pipelines."""
        sim = _build_sim_executor()
        sim_id = _setup_sim_active_limit(sim)
        sim_result = sim.modify_limit_order(order_id=sim_id, new_price=48000.0)

        live_mock = MockOrderExecution(mode=MockExecutionMode.DELAYED_FILL)
        live = live_mock.create_executor()
        live_id = _setup_live_active_limit(live_mock, live)
        live_result = live.modify_limit_order(order_id=live_id, new_price=48000.0)

        assert sim_result.success == live_result.success == True
        assert sim_result.status == live_result.status == ModificationStatus.PENDING
        assert sim_result.rejection_reason is None
        assert live_result.rejection_reason is None

    def test_in_flight_operation_set_in_both(self):
        """After modify, target.in_flight_operation == PENDING_MODIFY in both."""
        sim = _build_sim_executor()
        sim_id = _setup_sim_active_limit(sim)
        sim.modify_limit_order(order_id=sim_id, new_price=48000.0)
        sim_target = next(p for p in sim._active_limit_orders if p.pending_order_id == sim_id)

        live_mock = MockOrderExecution(mode=MockExecutionMode.DELAYED_FILL)
        live = live_mock.create_executor()
        live_id = _setup_live_active_limit(live_mock, live)
        live.modify_limit_order(order_id=live_id, new_price=48000.0)
        live_target = next(p for p in live._active_limit_orders if p.pending_order_id == live_id)

        assert sim_target.in_flight_operation == live_target.in_flight_operation == PendingOperation.PENDING_MODIFY

    def test_modify_applied_after_resolve_in_both(self):
        """After resolve, entry_price reflects new value in both pipelines."""
        sim = _build_sim_executor()
        sim_id = _setup_sim_active_limit(sim)
        sim.modify_limit_order(order_id=sim_id, new_price=48000.0)
        _resolve_sim_pending(sim)
        sim_target = next(p for p in sim._active_limit_orders if p.pending_order_id == sim_id)

        live_mock = MockOrderExecution(mode=MockExecutionMode.DELAYED_FILL)
        live = live_mock.create_executor()
        live_id = _setup_live_active_limit(live_mock, live)
        live.modify_limit_order(order_id=live_id, new_price=48000.0)
        _resolve_live_pending(live_mock, live)
        live_target = next(p for p in live._active_limit_orders if p.pending_order_id == live_id)

        assert sim_target.entry_price == live_target.entry_price == 48000.0
        assert sim_target.in_flight_operation == live_target.in_flight_operation == PendingOperation.NONE

    def test_busy_rejection_in_both(self):
        """Second modify while first is in-flight → OPERATION_BUSY in both pipelines."""
        sim = _build_sim_executor()
        sim_id = _setup_sim_active_limit(sim)
        sim.modify_limit_order(order_id=sim_id, new_price=48000.0)
        sim_busy = sim.modify_limit_order(order_id=sim_id, new_price=47000.0)

        live_mock = MockOrderExecution(mode=MockExecutionMode.DELAYED_FILL)
        live = live_mock.create_executor()
        live_id = _setup_live_active_limit(live_mock, live)
        live.modify_limit_order(order_id=live_id, new_price=48000.0)
        live_busy = live.modify_limit_order(order_id=live_id, new_price=47000.0)

        assert sim_busy.success == live_busy.success == False
        assert sim_busy.rejection_reason == live_busy.rejection_reason == ModificationRejectionReason.OPERATION_BUSY

    def test_not_found_rejection_in_both(self):
        """Unknown order_id → LIMIT_ORDER_NOT_FOUND in both pipelines."""
        sim = _build_sim_executor()
        _feed_sim_tick(sim, msc=1000)
        sim_result = sim.modify_limit_order(order_id='X', new_price=48000.0)

        live_mock = MockOrderExecution(mode=MockExecutionMode.DELAYED_FILL)
        live = live_mock.create_executor()
        live_mock.feed_tick(live, bid=49999.0, ask=50001.0)
        live_result = live.modify_limit_order(order_id='X', new_price=48000.0)

        assert sim_result.success == live_result.success == False
        assert sim_result.rejection_reason == live_result.rejection_reason == ModificationRejectionReason.LIMIT_ORDER_NOT_FOUND


# =============================================================================
# Cancel parity tests
# =============================================================================

class TestCancelContractParity:
    """cancel_limit_order returns identical bool + in_flight transitions in sim and live."""

    def test_cancel_accept_returns_true_in_both(self):
        """Valid cancel on active LIMIT → True in both pipelines."""
        sim = _build_sim_executor()
        sim_id = _setup_sim_active_limit(sim)
        sim_ok = sim.cancel_limit_order(order_id=sim_id)

        live_mock = MockOrderExecution(mode=MockExecutionMode.DELAYED_FILL)
        live = live_mock.create_executor()
        live_id = _setup_live_active_limit(live_mock, live)
        live_ok = live.cancel_limit_order(order_id=live_id)

        assert sim_ok == live_ok == True

    def test_in_flight_pending_cancel_set_in_both(self):
        """After cancel, target.in_flight_operation == PENDING_CANCEL in both."""
        sim = _build_sim_executor()
        sim_id = _setup_sim_active_limit(sim)
        sim.cancel_limit_order(order_id=sim_id)
        sim_target = next(p for p in sim._active_limit_orders if p.pending_order_id == sim_id)

        live_mock = MockOrderExecution(mode=MockExecutionMode.DELAYED_FILL)
        live = live_mock.create_executor()
        live_id = _setup_live_active_limit(live_mock, live)
        live.cancel_limit_order(order_id=live_id)
        live_target = next(p for p in live._active_limit_orders if p.pending_order_id == live_id)

        assert sim_target.in_flight_operation == live_target.in_flight_operation == PendingOperation.PENDING_CANCEL

    def test_cancel_removes_order_after_resolve_in_both(self):
        """After resolve, order is gone from _active_limit_orders in both pipelines."""
        sim = _build_sim_executor()
        sim_id = _setup_sim_active_limit(sim)
        sim.cancel_limit_order(order_id=sim_id)
        _resolve_sim_pending(sim)

        live_mock = MockOrderExecution(mode=MockExecutionMode.DELAYED_FILL)
        live = live_mock.create_executor()
        live_id = _setup_live_active_limit(live_mock, live)
        live.cancel_limit_order(order_id=live_id)
        _resolve_live_pending(live_mock, live)

        sim_gone = not any(p.pending_order_id == sim_id for p in sim._active_limit_orders)
        live_gone = not any(p.pending_order_id == live_id for p in live._active_limit_orders)
        assert sim_gone == live_gone == True

    def test_busy_returns_false_in_both(self):
        """Second cancel while first is in-flight → False in both pipelines."""
        sim = _build_sim_executor()
        sim_id = _setup_sim_active_limit(sim)
        sim.cancel_limit_order(order_id=sim_id)
        sim_busy = sim.cancel_limit_order(order_id=sim_id)

        live_mock = MockOrderExecution(mode=MockExecutionMode.DELAYED_FILL)
        live = live_mock.create_executor()
        live_id = _setup_live_active_limit(live_mock, live)
        live.cancel_limit_order(order_id=live_id)
        live_busy = live.cancel_limit_order(order_id=live_id)

        assert sim_busy == live_busy == False


# =============================================================================
# Capability gate parity
# =============================================================================

class TestCapabilityGateParity:
    """modify_stop_order / cancel_stop_order return same rejection in both pipelines for Kraken-style caps."""

    def test_modify_stop_order_unsupported_in_both(self):
        """Mock declares stop_orders=False → ORDER_TYPE_NOT_SUPPORTED in both."""
        sim = _build_sim_executor()
        _feed_sim_tick(sim, msc=1000)
        sim_result = sim.modify_stop_order(order_id='x', new_stop_price=50000.0)

        live_mock = MockOrderExecution(mode=MockExecutionMode.DELAYED_FILL)
        live = live_mock.create_executor()
        live_mock.feed_tick(live, bid=49999.0, ask=50001.0)
        live_result = live.modify_stop_order(order_id='x', new_stop_price=50000.0)

        assert sim_result.success == live_result.success == False
        assert sim_result.rejection_reason == live_result.rejection_reason == ModificationRejectionReason.ORDER_TYPE_NOT_SUPPORTED

    def test_cancel_stop_order_unsupported_in_both(self):
        """Mock declares stop_orders=False → cancel_stop_order returns False in both."""
        sim = _build_sim_executor()
        _feed_sim_tick(sim, msc=1000)
        sim_ok = sim.cancel_stop_order(order_id='x')

        live_mock = MockOrderExecution(mode=MockExecutionMode.DELAYED_FILL)
        live = live_mock.create_executor()
        live_mock.feed_tick(live, bid=49999.0, ask=50001.0)
        live_ok = live.cancel_stop_order(order_id='x')

        assert sim_ok == live_ok == False
