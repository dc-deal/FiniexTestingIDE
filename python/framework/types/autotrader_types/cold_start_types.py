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
from datetime import datetime
from enum import Enum, StrEnum
from typing import Any, Dict, List, Optional

from python.framework.types.composite_types import (
    CompositePart,
    ConfigChange,
    RunAxes,
    ValueOrigin,
)
from python.framework.types.persistence_types import ConfigurationCarryOver, PositionCarryOver
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
        key_is_ours: Whether the key names a session THIS bot has sent orders under, or None
            when there is no key of our shape to judge (FOREIGN_KEY). The reason alone cannot
            answer it: the symbol is tested BEFORE the session key, so an OTHER_SYMBOL order
            may carry either our own key or a stranger's and the bucket used to record
            neither. Anything that reasons about ownership — an algo hook, a report, a
            refusal — needs the fact rather than the string compare it was inferred from
    """
    reason: SkipReason
    client_order_id: Optional[str]
    broker_ref: str
    symbol: str
    direction: OrderDirection
    order_type: OrderType
    lots: float
    price: Optional[float] = None
    key_is_ours: Optional[bool] = None


@dataclass(frozen=True)
class BootPremiseViolation:
    """
    What the start found against the account's declared exclusivity, on the instrument this bot trades (#499).

    The profile declares the account this bot's alone, and orders carrying no key of ours rest on its
    own instrument — at spot both exposures merge into one balance, so the bot cannot size an order
    against what is actually its own. The session starts and holds its entries until the orders are
    gone or an operator accepts them.

    Args:
        symbol: The instrument they rest on — this bot's
        foreign_refs: The venue's references of those orders, none of them accepted by an operator yet
        detail: The sentence the hold, its alert and its record carry
        accepted_refs: Orders of another owner on the same instrument an operator accepted at an
            earlier start and that still rest — an acceptance at this start keeps them beside the new
            ones, since the carry-over holds one set
    """
    symbol: str
    foreign_refs: List[str]
    detail: str
    accepted_refs: List[str] = field(default_factory=list)


class BootFillClass(StrEnum):
    """
    How the boot read files one fill of ours against the book the carry-over wrote (#652).
    """
    # The book holds it: the order closed before the write and was answered before it, or it is
    # the entry of a position the book restored
    ACCOUNTED_FOR = 'accounted_for'
    # It opened a position the book does not hold: a buy, or at spot a sell, that no restored position
    # of the other side could take
    UNBOOKED_ENTRY = 'unbooked_entry'
    # It closed a position the book still holds: a sell a restored long can take, a buy a restored short
    UNBOOKED_CLOSE = 'unbooked_close'
    # The evidence names no single answer: a partial fill, a close instant inside the clock-skew band
    # around the write, or none at all
    UNCLEAR = 'unclear'
    # A market order of ours still working at the venue at the start: its fill may come yet, and a
    # session that opened meanwhile would buy the same position twice
    IN_FLIGHT = 'in_flight'

    def holds_entries(self) -> bool:
        """
        Whether a fill of this class keeps the session from opening anything new.

        Returns:
            True for every class but a fill the book already holds
        """
        return self != BootFillClass.ACCOUNTED_FOR


@dataclass(frozen=True)
class BootFill:
    """
    One order of ours the venue reported closed with a fill, as the boot read filed it (#652).

    Args:
        client_order_id: The wire key it was sent under
        broker_ref: The venue's reference
        symbol: Its instrument
        direction: Its side — LONG a buy, SHORT a sell
        lots: The size it was sent with
        filled_lots: What of it the venue executed
        closed_at: When the venue closed it; None where the venue reported no close instant
        fill_class: How it stands against the book
        reason: Why it was filed so, in a few words for the operator
    """
    client_order_id: str
    broker_ref: str
    symbol: str
    direction: OrderDirection
    lots: float
    filled_lots: float
    closed_at: Optional[datetime]
    fill_class: BootFillClass
    reason: str


@dataclass
class BootRead:
    """
    What a start read of the venue's closed orders since the carry-over was written (#652).

    A session killed between a market submit and its fill leaves the coins at the venue and nothing
    in its book; the next start reads which orders of ours the venue finished, and holds its entries
    over a fill the book does not hold, so it does not buy again.

    Args:
        written_at: The write the book was measured against: the carry-over's write, or while a
            hold of this kind stands, the write it was first measured against
        read_from: Where the read began: the earliest of that write and the oldest order open at
            it, minus the clock-skew margin
        read_to: Where it ended, the wall-clock now of the start
        complete: Whether the read reached the end of its range and gave an answer at all
        incomplete_reason: Why it did not, in a few words; '' when it did
        fills: Every closed order of ours with a fill inside the range, each filed
    """
    written_at: datetime
    read_from: datetime
    read_to: datetime
    complete: bool
    incomplete_reason: str = ''
    fills: List[BootFill] = field(default_factory=list)

    def holds_entries(self) -> bool:
        """
        Whether the session must start without opening anything new.

        Returns:
            True when the read did not complete, or found a fill the book does not account for
        """
        return not self.complete or any(fill.fill_class.holds_entries() for fill in self.fills)

    def get_holding_fills(self) -> List[BootFill]:
        """
        The fills the book does not account for, and the orders of ours still in flight at the start.

        Returns:
            Every entry whose class holds entries, in the order read
        """
        return [fill for fill in self.fills if fill.fill_class.holds_entries()]

    def describe(self) -> str:
        """
        The sentence a hold, its alert and its record carry.

        Returns:
            What the read found that the book does not hold, or why it could not tell
        """
        written = self.written_at.strftime('%Y-%m-%d %H:%M:%S UTC')
        parts: List[str] = []
        holding = self.get_holding_fills()
        if holding:
            listed = '; '.join(
                f"{'BUY' if fill.direction == OrderDirection.LONG else 'SELL'} {fill.filled_lots} "
                f'{fill.symbol} (order {fill.broker_ref}, '
                + (f"closed {fill.closed_at.strftime('%Y-%m-%d %H:%M:%S UTC')}"
                   if fill.closed_at is not None else 'no close instant')
                + f', {fill.fill_class.value}: {fill.reason})'
                for fill in holding)
            parts.append(f'{len(holding)} order(s) of ours that the book written at {written} does not '
                         f'hold — filled since, or still in flight at the venue: {listed}')
        if not self.complete:
            parts.append(f'the read of the venue\'s closed orders since {written} is incomplete: '
                         f'{self.incomplete_reason}')
        return '; and '.join(parts)


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
            base units. 0.0 when the account covers it, which is the normal case. A SURPLUS
            is never reported: on a shared account it belongs to whoever else uses it, and
            on one declared exclusive (#489) it is this bot's own untraded capital — the bot
            takes what is there. Either way there is nothing to correct
        applied: Whether the boot went on to APPLY what is listed here. False while the
            decision is still open, and False forever on a boot that refused — the lists then
            describe what WOULD have been adopted and restored, and a record that does not say
            so claims things that never happened
        boot_read: What the read of the venue's closed orders since the carry-over found (#652);
            None where the start read none — a dry run, a margin session, a first start
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
    boot_read: Optional[BootRead] = None

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
            # #652: a read that found a fill of ours, or could not finish, is a finding
            and (self.boot_read is None
                 or (self.boot_read.complete and not self.boot_read.fills))
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
            # #652: a fill of ours the book does not hold is the algo's to answer for
            and (self.boot_read is None or not self.boot_read.holds_entries())
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


class ConfigurationVerdict(StrEnum):
    """
    What a start found when it held its configuration against the one its carry-over's claims
    about the venue were made under (#626).
    """
    SAME = 'same'                        # one composite
    CHANGED = 'changed'                  # another composite
    PARTNER = 'partner'                  # a declared partner made the claims — exempt (#358)
    NOT_COMPARED = 'not_compared'        # the comparison could not be made; the reason says why
    NOTHING_CARRIED = 'nothing_carried'  # no claims and no record — a first start, or rehearsals only


@dataclass
class CurrentConfiguration:
    """
    What a starting session runs with — the side of the comparison no carry-over holds (#626).

    Args:
        profile_name: This profile's run unit name
        carry_over_from: The profile a declared continuation names, or ''
        deployment_id: The deployment the session continues or begins; '' for a one-off
        rendered_profile: What it runs with (#547); None when it could not be rendered
        account_currency: The account currency the start resolved
        run_axes: The resolved axes of its kind of run
        profile_path: The profile file, read for where a changed value comes from
        broker_type: The broker whose entry in `market_config.json` the session uses
    """
    profile_name: str
    carry_over_from: str
    deployment_id: str
    rendered_profile: Optional[Dict[str, Any]]
    account_currency: str
    run_axes: RunAxes
    profile_path: str
    broker_type: str


@dataclass
class ConfigurationComparison:
    """
    A starting session's configuration held against the one its carry-over recorded (#626).

    Args:
        verdict: What was found
        current: The starting session's side
        carried: The side the carry-over recorded; None when it recorded none
        changes: Every changed value that feeds the composite key, in path order
        changed_parts: The parts of the key that differ
        origins: Where each changed value comes from now, by setting
        one_sided: The changed settings only one of the two documents holds — a field the code
            added or dropped between the two sessions, which no file can undo
        current_key: The starting session's composite key — what `--accept-composite-change`
            names; '' when no key was computed
        reason: Why the comparison could not be made — '' unless NOT_COMPARED
    """
    verdict: ConfigurationVerdict
    current: CurrentConfiguration
    carried: Optional[ConfigurationCarryOver] = None
    changes: List[ConfigChange] = field(default_factory=list)
    changed_parts: List[CompositePart] = field(default_factory=list)
    origins: Dict[str, ValueOrigin] = field(default_factory=dict)
    one_sided: List[str] = field(default_factory=list)
    current_key: str = ''
    reason: str = ''

