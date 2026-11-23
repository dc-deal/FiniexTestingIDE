"""
FiniexTestingIDE - Entry Hold Types (#499)

An entry hold keeps a session running while it opens nothing new: it manages and closes its position,
moves its stops and targets, cancels its orders — and every order that would OPEN is refused at the
order path. Several causes hold entries the same way, so the cause is a closed set and each member
declares how a hold of its kind may end.

"Hold" here is the verb of withholding entries. It is not a holding — what an account possesses at
spot — and lifting a hold is not a release of the software.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Dict, FrozenSet, Optional


class EntryHoldLift(StrEnum):
    """
    How an entry hold ended.
    """
    # The cause measurably went away: a reconcile cycle that completed found none of what held it
    OBSERVED = 'observed'
    # A person lifted it, at a start they declared to watch (`--attended`)
    OPERATOR = 'operator'


class EntryHoldReason(StrEnum):
    """
    Why a session holds its entries. A new cause enters here, with its lift routes beside it.
    """
    # #499 — the profile declares the account exclusive, and at the start orders carrying no key of
    # ours rested on the very instrument this bot trades: at spot both exposures merge into one
    # balance, so this bot cannot size an order against what is actually its own
    BOOT_PREMISE = 'boot_premise'
    # #632 — the money record (the order events, the trades) could not be written: a position this
    # session opens now would exist nowhere but in memory, and the parity proof compares exactly this
    # record. Its lines wait in memory and are written again at the record's cadence
    RECORD_UNWRITABLE = 'record_unwritable'

    def lift_routes(self) -> FrozenSet[EntryHoldLift]:
        """
        The ways a hold of this reason may end. A route nobody performs would leave a hold that
        never lifts, so every route declared here has the code that performs it, held by a test.

        Returns:
            The routes this reason admits
        """
        return _LIFT_ROUTES[self]


# Beside the members they describe, held complete in both directions by
# tests/autotrader/order_guard/test_entry_holds.py
_LIFT_ROUTES: Dict[EntryHoldReason, FrozenSet[EntryHoldLift]] = {
    EntryHoldReason.BOOT_PREMISE: frozenset({EntryHoldLift.OBSERVED, EntryHoldLift.OPERATOR}),
    # Lifts when every line it kept is on the disk; an operator frees the disk, not the hold
    EntryHoldReason.RECORD_UNWRITABLE: frozenset({EntryHoldLift.OBSERVED}),
}


@dataclass(frozen=True)
class EntryHold:
    """
    One hold of a session, from its start to its end.

    Both instants are on the session's clock, which begins with its first market data. A start finds
    what holds it before then, so an instant can be empty — the way an order the boot adopted has no
    event time yet: a reading from the wall clock there would be a number that differs on every replay.

    Args:
        reason: Why entries are held
        held_at: When the hold began; None — from the session's start, before its clock began
        detail: What held them, in a sentence the operator can act on
        lifted_at: When it ended; None while it stands, and for a hold lifted at the start
        lifted_by: How it ended; None while it stands — and at the session's end, a hold still held
    """
    reason: EntryHoldReason
    held_at: Optional[datetime]
    detail: str
    lifted_at: Optional[datetime] = None
    lifted_by: Optional[EntryHoldLift] = None

    @property
    def is_active(self) -> bool:
        """
        Whether this hold still stands.

        Returns:
            True until it is lifted
        """
        return self.lifted_by is None
