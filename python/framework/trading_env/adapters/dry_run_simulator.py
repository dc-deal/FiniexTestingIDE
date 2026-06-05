"""
FiniexTestingIDE - Dry-Run Order Simulator

Shared utility for live-capable adapters operating in dry-run mode.
Provides a proper PENDING → FILLED lifecycle instead of instant-fill,
so the same pending-order tracking, polling, and reconciliation paths
run in dry-run as in live.

Lifecycle:
    submit  → PENDING with synthetic DRYRUN-NNNNNN ref
    query   → advances per-order poll counter; PENDING while counter > 0,
              FILLED on flip
    cancel  → CANCELLED, removed from state (idempotent)
    modify  → returns a NEW synthetic ref (mimics Kraken EditOrder
              replace-semantics); old ref is invalidated

The simulator is framework-agnostic — adapters compose it from their
Tier-3 transport layers (_do_request_*) when self._dry_run is True.
Real-mode transport remains untouched.

fill_price falls back to the order's limit price (LIMIT) or 0.0
(MARKET). MARKET dry-run with fill_price=0.0 is a documented limitation
— tick-aware dry-run fills are out of scope for this utility.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Optional

from python.framework.types.live_types.live_execution_types import (
    BrokerOrderStatus,
    BrokerResponse,
)


@dataclass
class _DryRunOrderState:
    """Per-order state tracked by the simulator."""
    lots: float
    price: Optional[float]
    remaining_polls: int


class DryRunOrderSimulator:
    """
    Stateful dry-run lifecycle simulator.

    One instance per adapter — counter and order state are isolated so
    parallel adapters do not share refs.
    """

    def __init__(self, polls_until_fill: int = 2):
        """
        Args:
            polls_until_fill: How many query() calls a PENDING order must
                              receive before flipping to FILLED. Default 2
                              matches the typical tick-loop cadence
                              (one tick → poll → still pending → next
                              tick → poll → fill).
        """
        self._counter: int = 0
        self._polls_until_fill = polls_until_fill
        self._orders: Dict[str, _DryRunOrderState] = {}

    def submit(
        self,
        lots: float,
        price: Optional[float],
        timestamp: datetime,
    ) -> BrokerResponse:
        """
        Register a new dry-run order. Returns PENDING with a synthetic ref.

        Args:
            lots: Order size (preserved for the eventual fill)
            price: Limit price (LIMIT) or None (MARKET)
            timestamp: Response timestamp (UTC)

        Returns:
            BrokerResponse(status=PENDING, broker_ref=DRYRUN-NNNNNN)
        """
        self._counter += 1
        broker_ref = f'DRYRUN-{self._counter:06d}'
        self._orders[broker_ref] = _DryRunOrderState(
            lots=lots,
            price=price,
            remaining_polls=self._polls_until_fill,
        )
        return BrokerResponse(
            broker_ref=broker_ref,
            status=BrokerOrderStatus.PENDING,
            timestamp=timestamp,
        )

    def query(self, broker_ref: str, timestamp: datetime) -> BrokerResponse:
        """
        Poll a dry-run order. Advances the per-order counter; flips to
        FILLED when the counter hits zero.

        Unknown broker_refs (e.g. already filled and removed, or one this
        simulator never issued) are reported as FILLED — matches the
        legacy "DRYRUN-* always FILLED on query" behavior so callers that
        re-query after a fill do not see false PENDING.

        Args:
            broker_ref: Synthetic DRYRUN-* reference
            timestamp: Response timestamp (UTC)

        Returns:
            BrokerResponse — PENDING while remaining_polls > 0, FILLED on flip
        """
        order = self._orders.get(broker_ref)
        if order is None:
            return BrokerResponse(
                broker_ref=broker_ref,
                status=BrokerOrderStatus.FILLED,
                timestamp=timestamp,
            )

        order.remaining_polls -= 1
        if order.remaining_polls > 0:
            return BrokerResponse(
                broker_ref=broker_ref,
                status=BrokerOrderStatus.PENDING,
                timestamp=timestamp,
            )

        # MARKET dry-run has no real fill price — fall back to 0.0 with the
        # downstream warning in LiveRequestProcessor.mark_filled.
        fill_price = order.price if order.price is not None else 0.0
        filled_lots = order.lots
        self._orders.pop(broker_ref, None)
        return BrokerResponse(
            broker_ref=broker_ref,
            status=BrokerOrderStatus.FILLED,
            fill_price=fill_price,
            filled_lots=filled_lots,
            timestamp=timestamp,
        )

    def cancel(self, broker_ref: str, timestamp: datetime) -> BrokerResponse:
        """
        Cancel a dry-run order. Idempotent — removes from state if
        present, always returns CANCELLED.

        Args:
            broker_ref: Synthetic DRYRUN-* reference
            timestamp: Response timestamp (UTC)

        Returns:
            BrokerResponse(status=CANCELLED)
        """
        self._orders.pop(broker_ref, None)
        return BrokerResponse(
            broker_ref=broker_ref,
            status=BrokerOrderStatus.CANCELLED,
            timestamp=timestamp,
        )

    def modify(
        self,
        broker_ref: str,
        new_price: Optional[float],
        timestamp: datetime,
    ) -> BrokerResponse:
        """
        Modify a dry-run order in-place. Mirrors Kraken AmendOrder
        semantics — the order keeps the SAME ref; only the price is
        applied (per-order state lots/remaining_polls is preserved).

        Args:
            broker_ref: Synthetic DRYRUN-* reference (unchanged by the amend)
            new_price: New limit price (None = keep current)
            timestamp: Response timestamp (UTC)

        Returns:
            BrokerResponse(status=PENDING, broker_ref unchanged)
        """
        order = self._orders.get(broker_ref)
        if order is not None and new_price is not None:
            order.price = new_price
        return BrokerResponse(
            broker_ref=broker_ref,
            status=BrokerOrderStatus.PENDING,
            timestamp=timestamp,
        )
