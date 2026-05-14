"""
Sim Async Cancel Path — Lifecycle Tests (#318)

Asserts the sim-side cancel lifecycle: scheduling sets in_flight_operation
to PENDING_CANCEL, Phase 0 of next-tick processing removes the order from
_active_limit_orders / _active_stop_orders.

Symmetric to test_async_cancel.py in live_executor — same algo-facing
contract, sim-specific resolution mechanism (msc-clock).
"""

from python.framework.types.trading_env_types.latency_simulator_types import PendingOperation
from python.framework.types.trading_env_types.order_types import (
    OpenOrderRequest,
    OrderDirection,
    OrderType,
)

from tests.simulation.modify_lifecycle.conftest import feed_sim_tick


def _submit_limit_to_active(executor, msc=1000, price=49000.0):
    """Submit a LIMIT order and feed one tick so it lands in _active_limit_orders."""
    feed_sim_tick(executor, msc=msc)
    result = executor.open_order(OpenOrderRequest(
        symbol='BTCUSD', order_type=OrderType.LIMIT,
        direction=OrderDirection.LONG, lots=0.001, price=price,
    ))
    feed_sim_tick(executor, msc=msc + 1)
    return result.order_id


class TestCancelLimitOrderAsyncLifecycle:
    """The sim cancel lifecycle: True return → next tick removes → state cleared."""

    def test_cancel_returns_true_when_scheduled(self, sim_executor):
        """cancel_limit_order returns True for a valid, resting, idle order."""
        order_id = _submit_limit_to_active(sim_executor)
        scheduled = sim_executor.cancel_limit_order(order_id=order_id)
        assert scheduled is True

    def test_in_flight_operation_set_during_window(self, sim_executor):
        """After cancel scheduling, target.in_flight_operation == PENDING_CANCEL."""
        order_id = _submit_limit_to_active(sim_executor)

        sim_executor.cancel_limit_order(order_id=order_id)

        target = next(p for p in sim_executor._active_limit_orders
                      if p.pending_order_id == order_id)
        assert target.in_flight_operation == PendingOperation.PENDING_CANCEL
        assert target.cancel_apply_at_msc is not None

    def test_order_removed_from_active_after_resolve(self, sim_executor):
        """Next feed_tick Phase 0 removes the order from _active_limit_orders."""
        order_id = _submit_limit_to_active(sim_executor)
        before = len(sim_executor._active_limit_orders)

        sim_executor.cancel_limit_order(order_id=order_id)
        feed_sim_tick(sim_executor, msc=2000)

        after = len(sim_executor._active_limit_orders)
        assert after == before - 1
        assert not any(p.pending_order_id == order_id
                       for p in sim_executor._active_limit_orders)


class TestCancelLimitOrderBusy:
    """One in-flight operation per order — second call returns False."""

    def test_second_cancel_returns_false(self, sim_executor):
        """Second cancel while first is in-flight → False (busy)."""
        order_id = _submit_limit_to_active(sim_executor)

        first = sim_executor.cancel_limit_order(order_id=order_id)
        assert first is True

        second = sim_executor.cancel_limit_order(order_id=order_id)
        assert second is False

    def test_cancel_during_pending_modify_returns_false(self, sim_executor):
        """Cancel on order with PENDING_MODIFY in flight → False (busy)."""
        order_id = _submit_limit_to_active(sim_executor)

        mod_result = sim_executor.modify_limit_order(order_id=order_id, new_price=48000.0)
        assert mod_result.success is True

        cancel_scheduled = sim_executor.cancel_limit_order(order_id=order_id)
        assert cancel_scheduled is False


class TestCancelLimitOrderNotFound:
    """Unknown order_id returns False."""

    def test_cancel_nonexistent_order(self, sim_executor):
        feed_sim_tick(sim_executor, msc=1000)
        scheduled = sim_executor.cancel_limit_order(order_id='NONEXISTENT')
        assert scheduled is False


class TestCancelStopOrderCapabilityGate:
    """cancel_stop_order rejected for adapters without STOP capability."""

    def test_cancel_stop_order_returns_false_for_kraken_profile(self, sim_executor):
        """Mock declares stop_orders=False → cancel_stop_order returns False."""
        feed_sim_tick(sim_executor, msc=1000)
        scheduled = sim_executor.cancel_stop_order(order_id='anything')
        assert scheduled is False
