"""
FiniexTestingIDE - OrderGuard Unit Tests

Isolated tests for OrderGuard — no executor, no scenario runner.
Covers cooldown arming, direction isolation, cooldown expiry,
and reset semantics.
"""

from datetime import datetime, timedelta, timezone

from python.framework.trading_env.order_guard import OrderGuard
from python.framework.types.trading_env_types.order_types import (
    OpenOrderRequest,
    OrderDirection,
    OrderType,
    RejectionReason,
)


_T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def _request(direction: OrderDirection) -> OpenOrderRequest:
    """Build a minimal market-order request for the given direction."""
    return OpenOrderRequest(
        symbol='BTCUSD',
        order_type=OrderType.MARKET,
        direction=direction,
        lots=0.1,
    )


class TestCooldown:
    """Rejection cooldown — stateful per direction."""

    def test_single_rejection_does_not_trigger_cooldown(self):
        guard = OrderGuard(max_consecutive_rejections=2)
        guard.record_rejection(OrderDirection.LONG, _T0)

        assert not guard.is_direction_blocked(OrderDirection.LONG, _T0)
        assert guard.validate(_request(OrderDirection.LONG), _T0) is None

    def test_threshold_rejections_trigger_cooldown(self):
        guard = OrderGuard(
            max_consecutive_rejections=2,
            cooldown_seconds=60.0,
        )
        guard.record_rejection(OrderDirection.LONG, _T0)
        guard.record_rejection(OrderDirection.LONG, _T0)

        assert guard.is_direction_blocked(OrderDirection.LONG, _T0)
        result = guard.validate(_request(OrderDirection.LONG), _T0)
        assert result is not None
        assert result.rejection_reason == RejectionReason.REJECTION_COOLDOWN
        assert result.order_id.startswith('guard_')

    def test_cooldown_is_direction_specific(self):
        guard = OrderGuard(max_consecutive_rejections=2)
        guard.record_rejection(OrderDirection.LONG, _T0)
        guard.record_rejection(OrderDirection.LONG, _T0)

        # LONG blocked, SHORT unaffected
        assert guard.is_direction_blocked(OrderDirection.LONG, _T0)
        assert not guard.is_direction_blocked(OrderDirection.SHORT, _T0)
        assert guard.validate(_request(OrderDirection.SHORT), _T0) is None

    def test_record_success_resets_counter_and_clears_cooldown(self):
        guard = OrderGuard(max_consecutive_rejections=2)
        guard.record_rejection(OrderDirection.LONG, _T0)
        guard.record_rejection(OrderDirection.LONG, _T0)
        assert guard.is_direction_blocked(OrderDirection.LONG, _T0)

        guard.record_success(OrderDirection.LONG)

        assert not guard.is_direction_blocked(OrderDirection.LONG, _T0)
        assert guard.validate(_request(OrderDirection.LONG), _T0) is None

    def test_cooldown_expires_after_duration(self):
        guard = OrderGuard(
            max_consecutive_rejections=2,
            cooldown_seconds=60.0,
        )
        guard.record_rejection(OrderDirection.LONG, _T0)
        guard.record_rejection(OrderDirection.LONG, _T0)
        assert guard.is_direction_blocked(OrderDirection.LONG, _T0)

        # Advance the (simulated) clock past the cooldown window
        future = _T0 + timedelta(seconds=61)
        assert not guard.is_direction_blocked(OrderDirection.LONG, future)
        assert guard.validate(_request(OrderDirection.LONG), future) is None

    def test_rejection_count_accumulates_across_successes_until_reset(self):
        """Only explicit reset (record_success) clears the counter."""
        guard = OrderGuard(max_consecutive_rejections=3)
        guard.record_rejection(OrderDirection.LONG, _T0)
        guard.record_rejection(OrderDirection.LONG, _T0)
        assert not guard.is_direction_blocked(OrderDirection.LONG, _T0)

        guard.record_rejection(OrderDirection.LONG, _T0)
        assert guard.is_direction_blocked(OrderDirection.LONG, _T0)

    def test_cooldown_anchored_to_simulated_now(self):
        """
        Cooldown duration is measured from the `now` passed in record_rejection,
        not from wall-clock time. A backtest feeding simulated timestamps must
        see cooldowns that span exactly `cooldown_seconds` of simulated time.
        """
        guard = OrderGuard(
            max_consecutive_rejections=2,
            cooldown_seconds=60.0,
        )
        guard.record_rejection(OrderDirection.LONG, _T0)
        guard.record_rejection(OrderDirection.LONG, _T0)

        # 59s later (sim time) — still blocked
        assert guard.is_direction_blocked(
            OrderDirection.LONG, _T0 + timedelta(seconds=59)
        )
        # 60.1s later — free
        assert not guard.is_direction_blocked(
            OrderDirection.LONG, _T0 + timedelta(seconds=60, milliseconds=100)
        )


class TestConfigurableThreshold:
    """Cooldown parameters honour configuration."""

    def test_custom_max_consecutive_rejections(self):
        guard = OrderGuard(max_consecutive_rejections=1)
        guard.record_rejection(OrderDirection.LONG, _T0)
        assert guard.is_direction_blocked(OrderDirection.LONG, _T0)

    def test_custom_cooldown_duration_message(self):
        guard = OrderGuard(
            max_consecutive_rejections=1,
            cooldown_seconds=5.0,
        )
        guard.record_rejection(OrderDirection.LONG, _T0)
        result = guard.validate(_request(OrderDirection.LONG), _T0)
        assert result is not None
        assert 'cooldown' in result.rejection_message.lower()
