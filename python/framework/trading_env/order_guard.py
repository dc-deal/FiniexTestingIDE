"""
FiniexTestingIDE - Order Guard
Pre-validation layer between DecisionLogic and the trade executor.

Enforces two runtime safety rules universally for CORE and USER decisions:

1. SHORT+SPOT guard — rejects SHORT orders in spot mode (no short selling).
2. Rejection cooldown — blocks a direction after N consecutive broker rejections
   for a configurable period, preventing rejection spam.

The guard sits inside DecisionTradingApi.send_order() and returns a fully-formed
OrderResult(REJECTED) on block — the executor is never called for blocked orders.
Guard rejections are recorded in the executor's order history via
AbstractTradeExecutor.record_guard_rejection() so batch reports see them
alongside real broker rejections.

State updates flow through two paths:
- Synchronous: direct rejections from open_order() (lot validation, adapter error)
  are handled in send_order() immediately.
- Asynchronous: outcomes that happen after PENDING return (margin rejection at
  fill time, successful fill after latency) flow via the executor's
  order_outcome_callback → DecisionTradingApi._on_order_outcome().
"""

from datetime import datetime, timedelta, timezone
from typing import Dict, Optional
from uuid import uuid4

from python.framework.types.market_types.market_config_types import TradingModel
from python.framework.types.trading_env_types.order_types import (
    OpenOrderRequest,
    OrderDirection,
    OrderResult,
    RejectionReason,
    create_rejection_result,
)


class OrderGuard:
    """
    Pre-validation guard for DecisionTradingApi.

    Stateful — tracks consecutive rejections per direction and enforces
    cooldowns independently for LONG and SHORT.
    """

    def __init__(
        self,
        trading_model: TradingModel,
        cooldown_seconds: float = 60.0,
        max_consecutive_rejections: int = 2,
    ):
        """
        Args:
            trading_model: SPOT or MARGIN — determines SHORT+SPOT blocking
            cooldown_seconds: Cooldown duration after max_consecutive_rejections is reached
            max_consecutive_rejections: Consecutive rejections per direction before cooldown triggers
        """
        self._trading_model = trading_model
        self._cooldown_seconds = cooldown_seconds
        self._max_consecutive_rejections = max_consecutive_rejections
        self._rejection_counts: Dict[OrderDirection, int] = {}
        self._cooldown_until: Dict[OrderDirection, datetime] = {}

    # ============================================
    # Validation
    # ============================================

    def validate(self, request: OpenOrderRequest) -> Optional[OrderResult]:
        """
        Pre-validate an order request.

        Args:
            request: Bundled order parameters

        Returns:
            OrderResult(REJECTED) if blocked, None if the order may proceed
        """
        # 1. SHORT+SPOT guard
        if (request.direction == OrderDirection.SHORT
                and self._trading_model == TradingModel.SPOT):
            return create_rejection_result(
                order_id=self._make_order_id(),
                reason=RejectionReason.SPOT_SHORT_BLOCKED,
                message='SHORT orders are not supported in spot mode',
            )

        # 2. Rejection cooldown guard
        cooldown_until = self._cooldown_until.get(request.direction)
        if cooldown_until is not None and cooldown_until > datetime.now(timezone.utc):
            remaining = (cooldown_until - datetime.now(timezone.utc)).total_seconds()
            return create_rejection_result(
                order_id=self._make_order_id(),
                reason=RejectionReason.REJECTION_COOLDOWN,
                message=(
                    f'{request.direction.value.upper()} blocked by rejection cooldown '
                    f'({remaining:.1f}s remaining)'
                ),
            )

        return None

    # ============================================
    # State updates (called by DecisionTradingApi)
    # ============================================

    def record_rejection(self, direction: OrderDirection) -> None:
        """Increment consecutive rejection counter and arm cooldown on threshold."""
        count = self._rejection_counts.get(direction, 0) + 1
        self._rejection_counts[direction] = count

        if count >= self._max_consecutive_rejections:
            self._cooldown_until[direction] = (
                datetime.now(timezone.utc)
                + timedelta(seconds=self._cooldown_seconds)
            )

    def record_success(self, direction: OrderDirection) -> None:
        """Reset rejection state for a direction after a successful submission."""
        self._rejection_counts[direction] = 0
        self._cooldown_until.pop(direction, None)

    def is_direction_blocked(self, direction: OrderDirection) -> bool:
        """Return True if direction is currently in cooldown."""
        cooldown_until = self._cooldown_until.get(direction)
        if cooldown_until is None:
            return False
        return cooldown_until > datetime.now(timezone.utc)

    # ============================================
    # Internals
    # ============================================

    @staticmethod
    def _make_order_id() -> str:
        """Distinct prefix makes guard rejections identifiable in logs."""
        return f'guard_{uuid4().hex[:8]}'
