"""
FiniexTestingIDE - Order Guard
Spam protection layer in the intermediate layer between DecisionLogic and executor.

The OrderGuard has a single responsibility: catch repeated broker failures and
prevent rejection storms. It does NOT enforce business rules, does NOT know
about market types, does NOT know about balances.

Structural validation (market type, balance, order type compatibility) belongs
in the executor, not in the guard.

Current rules:
- Rejection cooldown — blocks a direction after N consecutive broker rejections
  for a configurable period, preventing rejection spam (e.g. repeated
  INSUFFICIENT_MARGIN attempts).
- Stale-market-data block (#436) — rejects NEW entries while the session-level
  market-data status is stale (industry pre-trade risk practice: never open on
  blind data). Closes/cancels bypass send_order and stay unaffected — the
  risk-reducing asymmetry is deliberate. The framework floor under the
  mandatory on_market_data_stale hook; disable via
  order_guard.block_stale_market_data.

The guard sits inside DecisionTradingApi.send_order() and returns a fully-formed
OrderResult(DENIED) on block — the executor is never called for blocked orders.
Guard refusals are recorded in the executor's order history via
AbstractTradeExecutor.record_guard_rejection() so batch reports see them
beside the executor's own denials.

Time source:
- The guard is clock-agnostic — all time-dependent methods take an explicit
  `now: datetime` parameter supplied by the caller. In backtesting this is the
  current simulated tick timestamp (ensures determinism and sim-correct
  cooldown durations). In a live-adapter session it is the wall-clock tick timestamp
  (effectively datetime.now()). The guard never calls datetime.now() itself.

State updates flow through two paths:
- Synchronous: direct rejections from open_order() (lot validation, adapter error)
  are handled in send_order() immediately.
- Asynchronous: outcomes that happen after PENDING return (margin rejection at
  fill time, successful fill after latency) flow via the executor's
  order_outcome_callback → DecisionTradingApi._on_order_outcome().
"""

from datetime import datetime, timedelta
from typing import Dict, Optional, Set

from python.framework.types.trading_env_types.market_data_status_types import MarketDataStatus
from python.framework.types.trading_env_types.order_types import (
    OpenOrderRequest,
    OrderAction,
    OrderDirection,
    OrderResult,
    OrderStatus,
    RejectionReason,
    create_refusal_result,
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
        block_stale_market_data: bool = True,
    ):
        """
        Args:
            cooldown_seconds: Cooldown duration after max_consecutive_rejections is reached
            max_consecutive_rejections: Consecutive rejections per direction before cooldown triggers
            block_stale_market_data: Reject new entries while market data is stale (#436)
        """
        self._cooldown_seconds = cooldown_seconds
        self._max_consecutive_rejections = max_consecutive_rejections
        self._block_stale_market_data = block_stale_market_data
        self._rejection_counts: Dict[OrderDirection, int] = {}
        self._cooldown_until: Dict[OrderDirection, datetime] = {}
        self._denials_issued = 0

    # ============================================
    # Validation
    # ============================================

    def validate(
        self,
        request: OpenOrderRequest,
        now: datetime,
        market_data_status: Optional[MarketDataStatus] = None,
        unresolved_at_ceiling: Optional[Set[str]] = None,
    ) -> Optional[OrderResult]:
        """
        Pre-validate an order request against the guard rules.

        Args:
            request: Bundled order parameters
            now: Current tick time (simulated in backtests, wall-clock in live)
            market_data_status: Session-level tick-stream health (#436);
                always fresh in sim
            unresolved_at_ceiling: Orders the venue never named after the resolution ran
                out (#487). Non-empty blocks new entries; empty in sim and in the
                ordinary live case

        Returns:
            OrderResult(DENIED) if blocked, None otherwise
        """
        # Stale-market-data block (#436): never open new positions on blind
        # data. Only AutoTrader sessions can be stale; sim status is always fresh.
        if (
            self._block_stale_market_data
            and market_data_status is not None
            and market_data_status.is_stale
        ):
            return self._refuse(
                request, now,
                reason=RejectionReason.STALE_MARKET_DATA,
                message=(
                    f'Entry blocked: market data stale '
                    f'({market_data_status.seconds_since_last_tick:.0f}s '
                    f'since last tick)'
                ),
            )

        # Unresolved-write block (#487): we sent an order, asked the venue about it until
        # the resolution ran out, and it never named the order either open or closed. It may
        # be resting there. Sending MORE orders while one of ours is unaccounted for is the
        # case where trading helps least — so entries stop, and only entries: this guard is
        # reached from `validate` on an OpenOrderRequest, so closing and protecting an open
        # position are untouched. A LATCH, not a cooldown — the condition does not expire
        # with time, it ends when the order is finally accounted for.
        if unresolved_at_ceiling:
            return self._refuse(
                request, now,
                reason=RejectionReason.UNACCOUNTED_ORDER,
                message=(
                    f'Entry blocked: {len(unresolved_at_ceiling)} order(s) sent and never '
                    f'accounted for by the venue '
                    f"({', '.join(sorted(unresolved_at_ceiling))}) — check the account"
                ),
            )

        cooldown_until = self._cooldown_until.get(request.direction)
        if cooldown_until is not None and cooldown_until > now:
            remaining = (cooldown_until - now).total_seconds()
            return self._refuse(
                request, now,
                reason=RejectionReason.REJECTION_COOLDOWN,
                message=(
                    f'{request.direction.value.upper()} blocked by rejection cooldown '
                    f'({remaining:.1f}s remaining)'
                ),
            )

        return None

    def _refuse(
        self,
        request: OpenOrderRequest,
        now: datetime,
        reason: RejectionReason,
        message: str,
    ) -> OrderResult:
        """
        A guard refusal stating the entry it blocked, stamped at the guard's own time.

        Args:
            request: The blocked request
            now: The time the guard decided at
            reason: Which rule blocked it
            message: The human sentence beside the reason

        Returns:
            The DENIED OrderResult — the order never left this process
        """
        return create_refusal_result(
            order_id=self._make_order_id(),
            reason=reason,
            message=message,
            status=OrderStatus.DENIED,
            execution_time=now,
            action=OrderAction.OPEN,
            symbol=request.symbol,
            direction=request.direction,
            requested_lots=request.lots,
            order_type=request.order_type,
        )

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

    def _make_order_id(self) -> str:
        """
        The id of the next denial: a distinct prefix makes guard rejections identifiable in
        logs, and a COUNTER rather than a random suffix makes two identical backtests record
        identical ids — the order-event stream of one has to equal the other's (#362).

        Returns:
            `guard_<n>`, unique within this guard's unit
        """
        self._denials_issued += 1
        return f'guard_{self._denials_issued}'
