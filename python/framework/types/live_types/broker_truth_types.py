"""
FiniexTestingIDE - Broker-Truth Types (#362)

What the venue reports when a live session asks it: its resting orders, its balance sheet and, on
a margin account, its positions. Written into the session's order-event stream as the
`broker_truth` plane, beside what the session itself did and was told.

Live only — a backtest's venue is the simulation, whose book IS the truth, so there is nothing to
ask.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional

from python.framework.types.live_types.reconciliation_types import (
    BrokerOrder,
    BrokerPosition,
    ReconcileDivergence,
    ReconcileState,
)
from python.framework.types.trading_env_types.order_event_types import OrderEventPlane


class BrokerTruthReadReason(Enum):
    """Why the venue was asked."""
    SESSION_START = 'session_start'    # after the cold start, before the tick source starts
    SESSION_END = 'session_end'        # after the session-end orders, before the stream closes
    RECONCILE = 'reconcile'            # the reconciliation picture changed


class BrokerTruthPart(Enum):
    """One part of a read — named, so a part the read gave up on can be listed."""
    VENUE_ORDERS = 'venue_orders'
    VENUE_BALANCES = 'venue_balances'
    VENUE_POSITIONS = 'venue_positions'


@dataclass
class BrokerTruthSnapshot:
    """
    What one read of the venue returned.

    Each part is in one of three states: a value — an empty one included, which says the venue
    holds nothing; None and listed in `unread_parts`, which says the read gave up on it; or None
    and unlisted, which says this occasion does not read it — positions on a spot account,
    balances on a reconcile record that does not cross between clean and divergent.

    Args:
        venue_orders: The orders the venue reports as open, unfiltered
        venue_balances: Asset → amount as the venue reports it, quote currency included
        venue_positions: The venue's positions (margin)
        unread_parts: The parts the read gave up on
    """
    venue_orders: Optional[List[BrokerOrder]] = None
    venue_balances: Optional[Dict[str, float]] = None
    venue_positions: Optional[List[BrokerPosition]] = None
    unread_parts: List[BrokerTruthPart] = field(default_factory=list)


@dataclass
class BrokerTruthRecord:
    """
    One broker-truth line of the order-event stream.

    Args:
        seq: The unit's stream position — the counter the order events use, so both planes read
            in the order they were written
        read_reason: Why the venue was asked
        snapshot: What it answered
        reconcile_state: On a reconcile record, where the cycle left the two books
        divergence: On a divergent reconcile record, the picture by identity
        event_time: The run's canonical clock; None before it has been set — the read at
            session start comes before the first tick
        ts_init: When this process saw the answer, on the wall clock
        record_plane: Always the venue's
    """
    seq: int
    read_reason: BrokerTruthReadReason
    snapshot: BrokerTruthSnapshot
    reconcile_state: Optional[ReconcileState] = None
    divergence: Optional[ReconcileDivergence] = None
    event_time: Optional[datetime] = None
    ts_init: Optional[datetime] = None
    record_plane: OrderEventPlane = OrderEventPlane.BROKER_TRUTH
