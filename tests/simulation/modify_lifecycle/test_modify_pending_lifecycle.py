"""
Sim Async Modify Path — Lifecycle Tests (#318)

Asserts the sim-side modify lifecycle: scheduling sets in_flight_operation,
Phase 0 of next-tick processing applies the modification, and state clears.

Symmetric to test_async_modify.py in live_executor — same algo-facing
contract, sim-specific resolution mechanism (msc-clock vs. worker-thread).
"""

from python.framework.types.trading_env_types.latency_simulator_types import PendingOperation
from python.framework.types.trading_env_types.order_types import (
    ModificationRejectionReason,
    ModificationStatus,
    OpenOrderRequest,
    OrderCapabilities,
    OrderDirection,
    OrderType,
)

from tests.simulation.modify_lifecycle.conftest import feed_sim_tick


def _submit_limit_to_active(executor, msc=1000, price=49000.0):
    """Submit a LIMIT order and feed one tick so it lands in _active_limit_orders.

    INSTANT_FILL + zero latency + price-not-reached → order moves from
    latency-pending to _active_limit_orders in a single tick.
    """
    feed_sim_tick(executor, msc=msc)
    result = executor.open_order(OpenOrderRequest(
        symbol='BTCUSD', order_type=OrderType.LIMIT,
        direction=OrderDirection.LONG, lots=0.001, price=price,
    ))
    # One more tick to drain the latency queue → order now in _active_limit_orders
    feed_sim_tick(executor, msc=msc + 1)
    return result.order_id


class TestModifyLimitOrderAsyncLifecycle:
    """The sim modify lifecycle: PENDING return → next tick applies → state cleared."""

    def test_modify_returns_pending_initially(self, sim_executor):
        """modify_limit_order returns success=True with status=PENDING."""
        order_id = _submit_limit_to_active(sim_executor)

        mod_result = sim_executor.modify_limit_order(
            order_id=order_id, new_price=48000.0)

        assert mod_result.success is True
        assert mod_result.status == ModificationStatus.PENDING
        assert mod_result.order_id == order_id
        assert mod_result.rejection_reason is None

    def test_in_flight_operation_set_during_window(self, sim_executor):
        """After modify scheduling, target.execution_state.in_flight_operation == PENDING_MODIFY."""
        order_id = _submit_limit_to_active(sim_executor)

        sim_executor.modify_limit_order(order_id=order_id, new_price=48000.0)

        target = next(p for p in sim_executor._active_limit_orders
                      if p.pending_order_id == order_id)
        assert target.execution_state.in_flight_operation == PendingOperation.PENDING_MODIFY
        assert target.execution_state.pending_modification is not None
        assert target.execution_state.pending_modification.new_price == 48000.0

    def test_modification_applied_after_next_tick(self, sim_executor):
        """Next feed_tick Phase 0 applies the modification to entry_price + SL + TP."""
        order_id = _submit_limit_to_active(sim_executor)

        sim_executor.modify_limit_order(
            order_id=order_id, new_price=48000.0,
            new_stop_loss=47000.0, new_take_profit=52000.0,
        )

        # Next tick at later msc → Phase 0 resolves
        feed_sim_tick(sim_executor, msc=2000)

        target = next(p for p in sim_executor._active_limit_orders
                      if p.pending_order_id == order_id)
        assert target.entry_price == 48000.0
        assert target.order_kwargs.get('stop_loss') == 47000.0
        assert target.order_kwargs.get('take_profit') == 52000.0

    def test_in_flight_clears_after_resolve(self, sim_executor):
        """After Phase 0 resolve, in_flight_operation == NONE, pending_modification cleared."""
        order_id = _submit_limit_to_active(sim_executor)

        sim_executor.modify_limit_order(order_id=order_id, new_price=48000.0)
        feed_sim_tick(sim_executor, msc=2000)

        target = next(p for p in sim_executor._active_limit_orders
                      if p.pending_order_id == order_id)
        assert target.execution_state.in_flight_operation == PendingOperation.NONE
        assert target.execution_state.pending_modification is None


class TestModifyLimitOrderBusy:
    """One in-flight operation per order — second call returns OPERATION_BUSY."""

    def test_second_modify_returns_busy(self, sim_executor):
        """Second modify while first is in-flight → OPERATION_BUSY."""
        order_id = _submit_limit_to_active(sim_executor)

        first = sim_executor.modify_limit_order(order_id=order_id, new_price=48000.0)
        assert first.success is True

        second = sim_executor.modify_limit_order(order_id=order_id, new_price=47500.0)
        assert second.success is False
        assert second.rejection_reason == ModificationRejectionReason.OPERATION_BUSY

    def test_modify_during_pending_cancel_returns_busy(self, sim_executor):
        """Modify on order with PENDING_CANCEL in flight → OPERATION_BUSY."""
        order_id = _submit_limit_to_active(sim_executor)

        scheduled = sim_executor.cancel_limit_order(order_id=order_id)
        assert scheduled is True

        mod_result = sim_executor.modify_limit_order(order_id=order_id, new_price=48000.0)
        assert mod_result.success is False
        assert mod_result.rejection_reason == ModificationRejectionReason.OPERATION_BUSY


class TestModifyLimitOrderNotFound:
    """Unknown order_id returns LIMIT_ORDER_NOT_FOUND."""

    def test_modify_nonexistent_order(self, sim_executor):
        feed_sim_tick(sim_executor, msc=1000)
        mod_result = sim_executor.modify_limit_order(
            order_id='NONEXISTENT', new_price=48000.0)
        assert mod_result.success is False
        assert mod_result.rejection_reason == ModificationRejectionReason.LIMIT_ORDER_NOT_FOUND


class TestModifyStopOrderCapabilityGate:
    """modify_stop_order rejected for adapters without STOP capability."""

    def test_modify_stop_order_rejected_for_kraken_profile(self, sim_executor):
        """Mock declares stop_orders=False → ORDER_TYPE_NOT_SUPPORTED."""
        feed_sim_tick(sim_executor, msc=1000)
        mod_result = sim_executor.modify_stop_order(
            order_id='anything', new_stop_price=50000.0)
        assert mod_result.success is False
        assert mod_result.rejection_reason == ModificationRejectionReason.ORDER_TYPE_NOT_SUPPORTED


class TestModifyPositionCapabilityGate:
    """modify_position dual-mode: sync fallback (Kraken) vs. async pending (MT5)."""

    def test_modify_position_sync_fallback_for_kraken_caps(self, sim_executor):
        """native_position_sl_tp=False → instant portfolio.modify_position.

        With Kraken-style caps (default Mock), modify_position bypasses the
        async tracker and updates the portfolio directly. Returns SUCCESS,
        not PENDING — the legacy behavior is preserved for adapters without
        native attached SL/TP support.
        """
        # Setup: place a MARKET order so we have a position to modify
        feed_sim_tick(sim_executor, msc=1000)
        result = sim_executor.open_order(OpenOrderRequest(
            symbol='BTCUSD', order_type=OrderType.MARKET,
            direction=OrderDirection.LONG, lots=0.001,
        ))
        # Drain latency → position fills
        feed_sim_tick(sim_executor, msc=1001)
        positions = sim_executor.get_open_positions()
        assert len(positions) == 1
        position_id = positions[0].position_id

        # Modify SL/TP — should hit sync fallback (Mock declares native_position_sl_tp=False)
        mod_result = sim_executor.modify_position(
            position_id=position_id, new_stop_loss=48000.0, new_take_profit=52000.0)

        # Sync fallback returns SUCCESS, not PENDING. _pending_position_modifications stays empty.
        assert mod_result.success is True
        assert mod_result.status != ModificationStatus.PENDING
        assert len(sim_executor._pending_position_modifications) == 0

    def test_modify_position_async_path_with_mt5_caps(self, sim_executor):
        """native_position_sl_tp=True → async pending pattern + next-tick resolve.

        Monkey-patches the adapter capabilities to simulate MT5-style native
        attached SL/TP. modify_position schedules via _pending_position_modifications,
        next tick's Phase 0 applies via portfolio.modify_position.
        """
        # Setup: position
        feed_sim_tick(sim_executor, msc=1000)
        sim_executor.open_order(OpenOrderRequest(
            symbol='BTCUSD', order_type=OrderType.MARKET,
            direction=OrderDirection.LONG, lots=0.001,
        ))
        feed_sim_tick(sim_executor, msc=1001)
        positions = sim_executor.get_open_positions()
        position_id = positions[0].position_id

        # Override capability to simulate MT5 profile
        original = sim_executor.broker.adapter.get_order_capabilities
        def mt5_caps():
            caps = original()
            caps.native_position_sl_tp = True
            return caps
        sim_executor.broker.adapter.get_order_capabilities = mt5_caps

        mod_result = sim_executor.modify_position(
            position_id=position_id, new_stop_loss=48000.0, new_take_profit=52000.0)

        # Async path returns PENDING + tracker populated
        assert mod_result.success is True
        assert mod_result.status == ModificationStatus.PENDING
        assert position_id in sim_executor._pending_position_modifications

        # Next tick resolves
        feed_sim_tick(sim_executor, msc=2000)

        # Tracker drained, SL/TP applied to position
        assert position_id not in sim_executor._pending_position_modifications
        position = sim_executor.portfolio.get_position(position_id)
        assert position.stop_loss == 48000.0
        assert position.take_profit == 52000.0
