"""
FiniexTestingIDE - Cold-Start Types (#493)

What the boot situation looks like when it is handed to the decision logic, and what the
decision logic may answer.

The situation is READ-ONLY and it is complete on purpose: the whole account-wide list, not
only the part the framework acted on. A bot may well be able to account for an order it did
NOT get — and it cannot do that from a count. It is also the only channel through which an
algo ever learns about an order that was not adopted: `get_active_orders()` shows the bot its
own resting orders, and the reconciler reports the rest to the log, not to the algo.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

from python.framework.types.persistence_types import PositionCarryOver
from python.framework.types.trading_env_types.order_types import OrderDirection, OrderType


class SkipReason(Enum):
    """
    Why a resting order at the venue was left alone.

    Four answers, and they are not interchangeable: FOREIGN_KEY says the order is not ours,
    UNKNOWN_SESSION says it may well be ours and cannot be proven so, and the other two are
    ours but not adoptable in this shape.
    """
    FOREIGN_KEY = 'foreign_key'            # carries no client key of our shape
    UNKNOWN_SESSION = 'unknown_session'    # our SHAPE, a session this bot has no record of
    IN_FLIGHT = 'in_flight'                # our key, but a MARKET order: accepted, not resting
    OTHER_SYMBOL = 'other_symbol'          # our shape on an instrument this bot does not trade


@dataclass
class AdoptedOrder:
    """
    One resting order that was rebuilt into the executor's shadow.

    Args:
        order_id: The internal id, RECOVERED from the client key's counter
        client_order_id: The wire key the venue echoed back
        broker_ref: The venue's own reference
        symbol: The instrument
        direction: LONG / SHORT
        order_type: LIMIT / STOP / STOP_LIMIT
        lots: The ORIGINAL size the order was placed with
        filled_lots: How much the venue has already executed
        price: The resting price
    """
    order_id: str
    client_order_id: Optional[str]
    broker_ref: str
    symbol: str
    direction: OrderDirection
    order_type: OrderType
    lots: float
    filled_lots: float = 0.0
    price: Optional[float] = None


@dataclass
class SkippedOrder:
    """
    One resting order at the venue that was NOT adopted, and why.

    Args:
        reason: Which of the four answers applies
        client_order_id: The key it carries, when it carries one
        broker_ref: The venue's own reference
        symbol: The instrument — may differ from the bot's
        direction: LONG / SHORT
        order_type: What the venue reports it as
        lots: Order size
        price: The resting price
    """
    reason: SkipReason
    client_order_id: Optional[str]
    broker_ref: str
    symbol: str
    direction: OrderDirection
    order_type: OrderType
    lots: float
    price: Optional[float] = None


@dataclass
class ColdStartSituation:
    """
    Everything the boot step found, as the decision logic gets to see it (#493).

    Deliberately absent: a computed AGE of the carry-over. The stamp itself is provenance and
    is handed over; turning it into an age needs a "now", and at boot the canonical clock is
    not injected yet (§9). An age derived from the wall clock would be a decision hanging on
    a wall-clock read, which is exactly the boundary §9 draws.

    Args:
        symbol: The instrument this bot trades
        adopted: Resting orders rebuilt into the shadow
        skipped: Resting orders left alone, each with its reason — including other symbols,
            because they bind capital even though this bot does not trade them
        restored_positions: The book read back from the carry-over (spot only)
        carry_over_present: Whether a carry-over document was found and read
        carry_over_saved_at: When it was written, ISO-8601 UTC — provenance, not a verdict
        adoption_mode: The resolved policy ('auto' / 'operator_confirm')
        attended: Whether a human DECLARED they are watching this start
        book_shortfall: How much the restored book claims beyond what the account holds, in
            base units. 0.0 when the account covers it — which is the normal case, since a
            surplus belongs to whoever else uses the account
        applied: Whether the boot went on to APPLY what is listed here. False while the
            decision is still open, and False forever on a boot that refused — the lists then
            describe what WOULD have been adopted and restored, and a record that does not say
            so claims things that never happened
    """
    symbol: str
    adopted: List[AdoptedOrder] = field(default_factory=list)
    skipped: List[SkippedOrder] = field(default_factory=list)
    restored_positions: List[PositionCarryOver] = field(default_factory=list)
    carry_over_present: bool = False
    carry_over_saved_at: Optional[str] = None
    adoption_mode: str = ''
    attended: bool = False
    book_shortfall: float = 0.0
    applied: bool = False

    def is_empty(self) -> bool:
        """
        Whether this boot found nothing at all — not even somebody else's order.

        The difference from `is_clean()` matters at one place: an EMPTY situation is one
        nobody is asked about, while a CLEAN one may still carry a stranger's order and is
        worth handing over.

        Returns:
            True when there is nothing to report and nothing to ask about
        """
        return (
            not self.adopted
            and not self.skipped
            and not self.restored_positions
            and self.book_shortfall == 0.0
        )

    def is_clean(self) -> bool:
        """
        Whether this boot found nothing that needs accounting for.

        Clean means: nothing of ours was resting, nothing of our shape was unattributable, no
        position came back, and the account covers what the book claims. Orders belonging to
        somebody else do NOT make a boot unclean — they are none of this bot's business, and
        they are still listed so the algo can see them.

        Returns:
            True when there is nothing for the algo to answer for
        """
        return (
            not self.adopted
            and not self.restored_positions
            and self.book_shortfall == 0.0
            and not any(o.reason == SkipReason.UNKNOWN_SESSION for o in self.skipped)
        )


@dataclass
class ColdStartVerdict:
    """
    What a decision logic answers when it is asked about the boot situation (#493).

    A result type rather than a bare bool, because the two things an algo wants to say are
    different: whether it has ACCOUNTED for the situation, and WHY. The note is not decoration
    — it is what a reader sees in the run record after thirty restarts, where "True" alone
    would say nothing.

    `accounted_for` may only ever LOOSEN the framework's decision. The framework's refusal is
    the floor; True lifts it. The reverse would let an algo lock itself out of starting, which
    is a failure mode nobody asked for and which cannot be seen from the outside.

    A yes has to be SPECIFIC to be honoured: it must name every adopted order in
    `accounted_order_ids` and give a `note`. That is deliberate friction — it makes the author
    loop over `situation.adopted` and say something about each one, instead of returning a
    constant. Naming only some of them is refused: the framework cannot adopt half a book, and
    partial accounting is not accounting.

    Args:
        accounted_for: True = "I have accounted for this, let me start"
        note: Why — one line, and it reaches the session channel and the run record. Required
            for a yes; a yes that cannot be read back later is not an answer
        accounted_order_ids: The adopted orders this verdict speaks for. Must cover every one
            of them for a yes to count; ignored for a no
    """
    accounted_for: bool = False
    note: str = ''
    accounted_order_ids: List[str] = field(default_factory=list)
