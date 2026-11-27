"""
FiniexTestingIDE - Entry Holds (#499)

The holds of one session: which causes hold its entries right now, and every hold it had. The order
guard refuses a new entry while one stands; closing, protecting and cancelling stay free — the same
asymmetry as the unaccounted-order latch (#487), for the same reason: what reduces risk must never be
the thing that is blocked.

One gate with several holders. A cause holds at most once at a time; a second hold of the same cause
while the first stands changes nothing, because the first one's start is the fact the record needs.
"""

from dataclasses import replace
from datetime import datetime
from typing import List, Optional, Tuple

from python.framework.types.trading_env_types.entry_hold_types import (
    EntryHold,
    EntryHoldLift,
    EntryHoldReason,
)


class EntryHolds:
    """
    The entry holds of one session.

    A hold is placed at the boot and lifted by a reconcile cycle, both on the thread that reads the
    holds at every order. A holder that arrives from another thread — an operator's pause through
    the command channel (#648) — brings a lock with it.
    """

    def __init__(self):
        self._holds: List[EntryHold] = []

    def hold(self, reason: EntryHoldReason, at: Optional[datetime], detail: str) -> bool:
        """
        Hold the session's entries for a reason.

        Args:
            reason: Why
            at: When, on the session's clock; None at a start, before the clock began
            detail: What holds them, in a sentence the operator can act on

        Returns:
            True when this placed a hold; False when one of this reason already stands
        """
        if self.is_held(reason):
            return False
        self._holds.append(EntryHold(reason=reason, held_at=at, detail=detail))
        return True

    def lift(self, reason: EntryHoldReason, by: EntryHoldLift,
             at: Optional[datetime]) -> Optional[EntryHold]:
        """
        End the standing hold of a reason.

        Args:
            reason: Which hold
            by: How it ends — one of the routes the reason declares
            at: When, on the session's clock; None at a start, before the clock began

        Returns:
            The hold as it ended; None when none of this reason stood
        """
        if by not in reason.lift_routes():
            raise ValueError(f'A {reason.value} hold does not end by {by.value}; it ends by '
                             f"{', '.join(sorted(r.value for r in reason.lift_routes()))}")
        for index, hold in enumerate(self._holds):
            if hold.reason is reason and hold.is_active:
                lifted = replace(hold, lifted_at=at, lifted_by=by)
                self._holds[index] = lifted
                return lifted
        return None

    def is_held(self, reason: EntryHoldReason) -> bool:
        """
        Whether a hold of this reason stands.

        Args:
            reason: The reason asked about

        Returns:
            True while it stands
        """
        return any(h.reason is reason and h.is_active for h in self._holds)

    def get_active(self) -> Tuple[EntryHold, ...]:
        """
        The holds standing right now.

        Returns:
            Each standing hold, oldest first; empty when entries are free
        """
        return tuple(h for h in self._holds if h.is_active)

    def get_all(self) -> Tuple[EntryHold, ...]:
        """
        Every hold of the session, lifted ones included — what its record reports.

        Returns:
            Each hold in the order it began
        """
        return tuple(self._holds)
