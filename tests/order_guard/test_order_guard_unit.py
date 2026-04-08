"""
FiniexTestingIDE - OrderGuard Unit Tests

Isolated tests for OrderGuard — no executor, no scenario runner.
Covers SHORT+SPOT blocking, cooldown arming, direction isolation,
cooldown expiry, and reset semantics.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from python.framework.trading_env.order_guard import OrderGuard
from python.framework.types.market_types.market_config_types import TradingModel
from python.framework.types.trading_env_types.order_types import (
    OpenOrderRequest,
    OrderDirection,
    OrderStatus,
    OrderType,
    RejectionReason,
)


def _request(direction: OrderDirection) -> OpenOrderRequest:
    """Build a minimal market-order request for the given direction."""
    return OpenOrderRequest(
        symbol='BTCUSD',
        order_type=OrderType.MARKET,
        direction=direction,
        lots=0.1,
    )


class TestShortSpotBlocking:
    """SHORT+SPOT guard — structural block, no state."""

    def test_short_blocked_in_spot(self):
        guard = OrderGuard(trading_model=TradingModel.SPOT)
        result = guard.validate(_request(OrderDirection.SHORT))

        assert result is not None
        assert result.status == OrderStatus.REJECTED
        assert result.rejection_reason == RejectionReason.SPOT_SHORT_BLOCKED
        assert result.order_id.startswith('guard_')

    def test_long_passes_in_spot(self):
        guard = OrderGuard(trading_model=TradingModel.SPOT)
        assert guard.validate(_request(OrderDirection.LONG)) is None

    def test_short_passes_in_margin(self):
        guard = OrderGuard(trading_model=TradingModel.MARGIN)
        assert guard.validate(_request(OrderDirection.SHORT)) is None

    def test_long_passes_in_margin(self):
        guard = OrderGuard(trading_model=TradingModel.MARGIN)
        assert guard.validate(_request(OrderDirection.LONG)) is None


class TestCooldown:
    """Rejection cooldown — stateful per direction."""

    def test_single_rejection_does_not_trigger_cooldown(self):
        guard = OrderGuard(
            trading_model=TradingModel.MARGIN,
            max_consecutive_rejections=2,
        )
        guard.record_rejection(OrderDirection.LONG)

        assert not guard.is_direction_blocked(OrderDirection.LONG)
        assert guard.validate(_request(OrderDirection.LONG)) is None

    def test_threshold_rejections_trigger_cooldown(self):
        guard = OrderGuard(
            trading_model=TradingModel.MARGIN,
            max_consecutive_rejections=2,
            cooldown_seconds=60.0,
        )
        guard.record_rejection(OrderDirection.LONG)
        guard.record_rejection(OrderDirection.LONG)

        assert guard.is_direction_blocked(OrderDirection.LONG)
        result = guard.validate(_request(OrderDirection.LONG))
        assert result is not None
        assert result.rejection_reason == RejectionReason.REJECTION_COOLDOWN
        assert result.order_id.startswith('guard_')

    def test_cooldown_is_direction_specific(self):
        guard = OrderGuard(
            trading_model=TradingModel.MARGIN,
            max_consecutive_rejections=2,
        )
        guard.record_rejection(OrderDirection.LONG)
        guard.record_rejection(OrderDirection.LONG)

        # LONG blocked, SHORT unaffected
        assert guard.is_direction_blocked(OrderDirection.LONG)
        assert not guard.is_direction_blocked(OrderDirection.SHORT)
        assert guard.validate(_request(OrderDirection.SHORT)) is None

    def test_record_success_resets_counter_and_clears_cooldown(self):
        guard = OrderGuard(
            trading_model=TradingModel.MARGIN,
            max_consecutive_rejections=2,
        )
        guard.record_rejection(OrderDirection.LONG)
        guard.record_rejection(OrderDirection.LONG)
        assert guard.is_direction_blocked(OrderDirection.LONG)

        guard.record_success(OrderDirection.LONG)

        assert not guard.is_direction_blocked(OrderDirection.LONG)
        assert guard.validate(_request(OrderDirection.LONG)) is None

    def test_cooldown_expires_after_duration(self):
        guard = OrderGuard(
            trading_model=TradingModel.MARGIN,
            max_consecutive_rejections=2,
            cooldown_seconds=60.0,
        )
        guard.record_rejection(OrderDirection.LONG)
        guard.record_rejection(OrderDirection.LONG)
        assert guard.is_direction_blocked(OrderDirection.LONG)

        # Jump the clock past the cooldown window
        future = datetime.now(timezone.utc) + timedelta(seconds=61)
        with patch(
            'python.framework.trading_env.order_guard.datetime'
        ) as mock_datetime:
            mock_datetime.now.return_value = future
            mock_datetime.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

            assert not guard.is_direction_blocked(OrderDirection.LONG)
            assert guard.validate(_request(OrderDirection.LONG)) is None

    def test_rejection_count_accumulates_across_successes_until_reset(self):
        """Only explicit reset (record_success) clears the counter."""
        guard = OrderGuard(
            trading_model=TradingModel.MARGIN,
            max_consecutive_rejections=3,
        )
        guard.record_rejection(OrderDirection.LONG)
        guard.record_rejection(OrderDirection.LONG)
        assert not guard.is_direction_blocked(OrderDirection.LONG)

        guard.record_rejection(OrderDirection.LONG)
        assert guard.is_direction_blocked(OrderDirection.LONG)


class TestConfigurableThreshold:
    """Cooldown parameters honour configuration."""

    def test_custom_max_consecutive_rejections(self):
        guard = OrderGuard(
            trading_model=TradingModel.MARGIN,
            max_consecutive_rejections=1,
        )
        guard.record_rejection(OrderDirection.LONG)
        assert guard.is_direction_blocked(OrderDirection.LONG)

    def test_custom_cooldown_duration_message(self):
        guard = OrderGuard(
            trading_model=TradingModel.MARGIN,
            max_consecutive_rejections=1,
            cooldown_seconds=5.0,
        )
        guard.record_rejection(OrderDirection.LONG)
        result = guard.validate(_request(OrderDirection.LONG))
        assert result is not None
        assert 'cooldown' in result.rejection_message.lower()
