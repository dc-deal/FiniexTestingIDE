"""
FiniexTestingIDE - Order Guard
Spam protection layer in the intermediate layer between DecisionLogic and executor.

The OrderGuard has a single responsibility: catch repeated broker failures and
prevent rejection storms. It does NOT enforce business rules, does NOT know
about market types, does NOT know about balances.

Structural validation (market type, balance, order type compatibility) belongs
in the executor, not in the guard.

Current rule:
- Rejection cooldown — blocks a direction after N consecutive broker rejections
  for a configurable period, preventing rejection spam (e.g. repeated
  INSUFFICIENT_MARGIN attempts).

The guard sits inside DecisionTradingApi.send_order() and returns a fully-formed
OrderResult(REJECTED) on block — the executor is never called for blocked orders.
Guard rejections are recorded in the executor's order history via
AbstractTradeExecutor.record_guard_rejection() so batch reports see them
alongside real broker rejections.

Time source:
- The guard is clock-agnostic — all time-dependent methods take an explicit
  `now: datetime` parameter supplied by the caller. In backtesting this is the
  current simulated tick timestamp (ensures determinism and sim-correct
  cooldown durations). In live trading it is the wall-clock tick timestamp
  (effectively datetime.now()). The guard never calls datetime.now() itself.

State updates flow through two paths:
- Synchronous: direct rejections from open_order() (lot validation, adapter error)
  are handled in send_order() immediately.
- Asynchronous: outcomes that happen after PENDING return (margin rejection at
  fill time, successful fill after latency) flow via the executor's
  order_outcome_callback → DecisionTradingApi._on_order_outcome().
"""

from datetime import datetime, timedelta
from typing import Dict, Optional
from uuid import uuid4

from python.framework.types.trading_env_types.order_types import (
    OpenOrderRequest,
    OrderDirection,
    OrderResult,
    RejectionReason,
    create_rejection_result,
)


class OrderGuard:
    """
    Spam protection guard for DecisionTradingApi.

    Stateful — tracks consecutive rejections per direction and enforces
    cooldowns independently for LONG and SHORT.
    """

    def __init__(
        self,
        cooldown_seconds: float = 60.0,
        max_consecutive_rejections: int = 2,
    ):
        """
        Args:
            cooldown_seconds: Cooldown duration after max_consecutive_rejections is reached
            max_consecutive_rejections: Consecutive rejections per direction before cooldown triggers
        """
        self._cooldown_seconds = cooldown_seconds
        self._max_consecutive_rejections = max_consecutive_rejections
        self._rejection_counts: Dict[OrderDirection, int] = {}
        self._cooldown_until: Dict[OrderDirection, datetime] = {}

    # ============================================
    # Validation
    # ============================================

    def validate(
        self,
        request: OpenOrderRequest,
        now: datetime,
    ) -> Optional[OrderResult]:
        """
        Pre-validate an order request against the rejection cooldown.

        Args:
            request: Bundled order parameters
            now: Current tick time (simulated in backtests, wall-clock in live)

        Returns:
            OrderResult(REJECTED) if blocked by cooldown, None otherwise
        """
        cooldown_until = self._cooldown_until.get(request.direction)
        if cooldown_until is not None and cooldown_until > now:
            remaining = (cooldown_until - now).total_seconds()
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

    def record_rejection(self, direction: OrderDirection, now: datetime) -> None:
        """
        Increment consecutive rejection counter and arm cooldown on threshold.

        Args:
            direction: Direction that was rejected
            now: Current tick time — used as the cooldown anchor
        """
        count = self._rejection_counts.get(direction, 0) + 1
        self._rejection_counts[direction] = count

        if count >= self._max_consecutive_rejections:
            self._cooldown_until[direction] = (
                now + timedelta(seconds=self._cooldown_seconds)
            )

    def record_success(self, direction: OrderDirection) -> None:
        """Reset rejection state for a direction after a successful submission."""
        self._rejection_counts[direction] = 0
        self._cooldown_until.pop(direction, None)

    def is_direction_blocked(
        self,
        direction: OrderDirection,
        now: datetime,
    ) -> bool:
        """
        Return True if direction is currently in cooldown.

        Args:
            direction: Direction to check
            now: Current tick time

        Returns:
            True if cooldown is active for this direction
        """
        cooldown_until = self._cooldown_until.get(direction)
        if cooldown_until is None:
            return False
        return cooldown_until > now

    # ============================================
    # Internals
    # ============================================

    @staticmethod
    def _make_order_id() -> str:
        """Distinct prefix makes guard rejections identifiable in logs."""
        return f'guard_{uuid4().hex[:8]}'
