"""
FiniexTestingIDE - Shutdown Status Types

What a session's stop is doing right now (#630), in one frozen snapshot every surface reads: the
live display, the journal's lines and the closing alert, so each names a stop in the same words. A
stop can take as long as a supervisor allows, and it used to say nothing until its reports.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Dict, FrozenSet, Iterable, List, Optional, Sequence, Tuple

from python.framework.types.trading_env_types.awaited_order_types import AwaitedOrder
from python.framework.types.trading_env_types.order_types import (
    OrderAction,
    OrderResult,
    OrderStatus,
    OrderType,
)

# An order a stop waits for, told apart by its id and its action: a market close carries the id of
# the position it closes
AwaitedOrderKey = Tuple[str, OrderAction]

# Below this share of the lots sent, an execution counts as a part: float noise, not a missing lot
_PART_TOLERANCE = 1e-9


class ShutdownStep(Enum):
    """
    The steps of a session's stop, in the order it takes them.

    The declared order is the contract: what is still to do is the steps after the current one.
    Stopping the feeds is part of the first step, because it happens on the way out of the loop and
    nothing the operator has to weigh hangs on it.
    """
    LOOP_END = 'loop_end'
    CARRY_OVER = 'carry_over'
    ORDER_CLEANUP = 'order_cleanup'
    VENUE_READ = 'venue_read'
    REPORTS = 'reports'
    ALERTS = 'alerts'

    def label(self) -> str:
        """
        What the operator reads for this step.

        Returns:
            A few words
        """
        return SHUTDOWN_STEP_LABELS[self]

    def steps_after(self) -> List['ShutdownStep']:
        """
        The steps that follow this one.

        Returns:
            Them, in the order the stop takes them
        """
        steps = list(ShutdownStep)
        return steps[steps.index(self) + 1:]


SHUTDOWN_STEP_LABELS: Dict[ShutdownStep, str] = {
    ShutdownStep.LOOP_END: 'end of the loop, the feeds',
    ShutdownStep.CARRY_OVER: 'carry-over',
    ShutdownStep.ORDER_CLEANUP: 'orders to cancel',
    ShutdownStep.VENUE_READ: 'venue read',
    ShutdownStep.REPORTS: 'reports',
    ShutdownStep.ALERTS: 'alerts',
}


@dataclass(frozen=True)
class ShutdownStatus:
    """
    One moment of a session's stop.

    Ages are measured when it is read, against `as_of_monotonic_s`, so a surface renders from this
    snapshot alone and its ages keep moving between two writes of it.

    Args:
        asked_by: What asked for the stop, in a word or two
        asked_at: When it was asked for, UTC, for a person to read
        as_of_monotonic_s: `time.monotonic()` of this reading
        step: The step in progress
        kill_at_monotonic_s: When the declared kill falls, counted from the instant the stop was
            asked for; None when no kill is declared
        kill_signalled: Whether a signal asked for the stop, so that a supervisor's kill is coming.
            A stop nobody signalled keeps to the same time, as if a signal had come when it began
        tick_source_stopped: Whether the session stopped its tick source, so that a stream gone
            silent is no fault
        orders_to_cancel: How many resting orders the cleanup will cancel; None while no executor
            can say
        awaited_orders: What the stop waits for at this moment
        order_wait_began_monotonic_s: When the wait for them began; None while no wait runs
        order_wait_bound_s: How long that wait may run in all, as its last pass read it; None while
            no wait runs
        orders_waited_for: The key of every order a wait waited for, which outlasts the wait
        order_wait_ran_s: How long the wait ran; None while it runs, and when there was none
    """
    asked_by: str
    asked_at: datetime
    as_of_monotonic_s: float
    step: ShutdownStep = ShutdownStep.LOOP_END
    kill_at_monotonic_s: Optional[float] = None
    kill_signalled: bool = False
    tick_source_stopped: bool = False
    orders_to_cancel: Optional[int] = None
    awaited_orders: Tuple[AwaitedOrder, ...] = ()
    order_wait_began_monotonic_s: Optional[float] = None
    order_wait_bound_s: Optional[float] = None
    orders_waited_for: FrozenSet[AwaitedOrderKey] = frozenset()
    order_wait_ran_s: Optional[float] = None

    def headline(self) -> str:
        """
        What the stop is, in one line.

        Returns:
            For instance `🛑 SHUTTING DOWN · SIGTERM 14:22:42 UTC · decisions stopped`
        """
        return (f'🛑 SHUTTING DOWN · {self.asked_by} {self.asked_at:%H:%M:%S} UTC · '
                f'decisions stopped')

    def step_words(self, step: ShutdownStep) -> str:
        """
        What the operator reads for one step of this stop, with the count the cleanup carries.

        Args:
            step: The step

        Returns:
            A few words
        """
        if step is ShutdownStep.ORDER_CLEANUP and self.orders_to_cancel is not None:
            orders = 'order' if self.orders_to_cancel == 1 else 'orders'
            return f'{self.orders_to_cancel} {orders} to cancel'
        return step.label()

    def still_to_do(self) -> List[str]:
        """
        What the stop has yet to do after the step in progress.

        Returns:
            One entry per step, empty on the last
        """
        return [self.step_words(step) for step in self.step.steps_after()]

    def now_doing(self) -> Optional[str]:
        """
        The step in progress, where no order waited for already says it.

        Returns:
            Its words; None while the stop waits for an order
        """
        return None if self.awaited_orders else self.step_words(self.step)

    def step_line(self) -> str:
        """
        The journal's line for the step in progress.

        Returns:
            For instance `🛑 Stop: venue read · still to do: reports · alerts`
        """
        still_to_do = ' · '.join(self.still_to_do())
        rest = f' · still to do: {still_to_do}' if still_to_do else ''
        return f'🛑 Stop: {self.step_words(self.step)}{rest}'

    def order_lines(self) -> List[str]:
        """
        Each order the stop waits for, as the operator reads it.

        Returns:
            For instance `market BUY 0.12 ETHUSD · on its way 4.2 s · venue has not answered`. An
            order this session did not send has no age to name
        """
        lines = []
        for row in self.awaited_orders:
            sent_at = row.sent_at_monotonic_s
            age = (f' · on its way {self.as_of_monotonic_s - sent_at:.1f} s'
                   if sent_at is not None else '')
            lines.append(f'{row.describe()}{age} · {row.waits_for}')
        return lines

    def order_wait_text(self) -> Optional[str]:
        """
        How far the wait for the orders has come, and what follows it.

        Returns:
            For instance `4.2 s of 30.0 s, then: booked unaccounted, reported, session ends
            itself`; None while no wait runs
        """
        began = self.order_wait_began_monotonic_s
        if began is None or self.order_wait_bound_s is None:
            return None
        return (f'{self.as_of_monotonic_s - began:.1f} s of {self.order_wait_bound_s:.1f} s, '
                f'then: {self._what_follows_the_wait()}, reported, session ends itself')

    def _what_follows_the_wait(self) -> str:
        """
        What becomes of the orders still on their way when the wait runs out. A market order is
        booked unaccounted there and then; a resting order goes on to the cleanup, which cancels,
        leaves or books it by the session-end policy.

        Returns:
            A few words
        """
        resting = sum(1 for row in self.awaited_orders if row.order_type is not OrderType.MARKET)
        if not resting:
            return 'booked unaccounted'
        if resting == len(self.awaited_orders):
            return 'left to the cleanup'
        return 'market orders booked unaccounted, resting ones left to the cleanup'

    def kill_text(self) -> Optional[str]:
        """
        How long until the supervisor's declared kill.

        Returns:
            For instance `in 75.8 s (supervisor)`, `overdue by 2.0 s (supervisor)` once the declared
            time has gone by, or, for a stop nobody signalled, `not signalled, the stop keeps
            within 75.8 s`; None when no kill is declared
        """
        if self.kill_at_monotonic_s is None:
            return None
        left_s = self.kill_at_monotonic_s - self.as_of_monotonic_s
        if not self.kill_signalled:
            if left_s < 0:
                return f'not signalled, the stop is {-left_s:.1f} s past the time it keeps within'
            return f'not signalled, the stop keeps within {left_s:.1f} s'
        if left_s < 0:
            return f'overdue by {-left_s:.1f} s (supervisor)'
        return f'in {left_s:.1f} s (supervisor)'


@dataclass(frozen=True)
class AwaitedOrderEnding:
    """
    How one order a stop waited for ended, as its records say.

    Args:
        status: Its last ending; None for an order with no ending on the record, as a resting order
            the venue took has none
        executed_lots: What it executed, on an executed ending
        sent_lots: What it was sent with, from the row of its submission; None where that row is
            not on the record
    """
    status: Optional[OrderStatus]
    executed_lots: Optional[float] = None
    sent_lots: Optional[float] = None

    def is_filled_in_part(self) -> bool:
        """
        Whether it executed, but less than it was sent with.

        Returns:
            True for an executed ending below the lots sent
        """
        return (self.status is OrderStatus.EXECUTED
                and self.executed_lots is not None and self.sent_lots is not None
                and self.executed_lots < self.sent_lots * (1.0 - _PART_TOLERANCE))

    def describe(self) -> str:
        """
        The ending, as the operator reads it.

        Returns:
            For instance `executed`, `executed in part, 0.004 of 0.01` or `open at the venue`
        """
        if self.status is None:
            return 'open at the venue'
        if self.is_filled_in_part():
            return f'executed in part, {self.executed_lots:g} of {self.sent_lots:g}'
        return self.status.value


def endings_by_order(
    keys: Iterable[AwaitedOrderKey],
    order_history: Sequence[OrderResult],
) -> Dict[AwaitedOrderKey, AwaitedOrderEnding]:
    """
    How each of some orders ended, read off the session's order records.

    Args:
        keys: The orders, each by its id and its action
        order_history: The records, oldest first

    Returns:
        Each order's ending
    """
    last: Dict[AwaitedOrderKey, Optional[OrderResult]] = dict.fromkeys(keys)
    sent: Dict[AwaitedOrderKey, Optional[float]] = dict.fromkeys(last)
    for row in order_history:
        key = (row.order_id, row.action)
        if key not in last:
            continue
        if row.status is OrderStatus.PENDING:
            if sent[key] is None:
                sent[key] = row.requested_lots
        else:
            last[key] = row
    return {
        key: (AwaitedOrderEnding(status=None) if row is None else AwaitedOrderEnding(
            status=row.status, executed_lots=row.executed_lots, sent_lots=sent[key]))
        for key, row in last.items()
    }


@dataclass(frozen=True)
class StopWaitOutcome:
    """
    What a stop's wait for the orders on their way came to (#630).

    Read off the order records when the session's result is collected, so that it reports how each
    order ended, after the cleanup as well, and no counter has to be subtracted from another.

    Args:
        waited_s: How long the wait ran, on the monotonic clock
        orders_executed: Waited for, and `executed` in full, and booked
        orders_filled_in_part: Waited for, and `executed` below the lots sent: the part is booked,
            and the rest did not execute or nobody knows whether it did
        orders_unaccounted: Waited for, and `unaccounted`: the venue did not say
        orders_ended_unfilled: Waited for, and any other ending: refused, cancelled, expired
        orders_still_open: Waited for, and with no ending on the record: a resting order the venue
            took, which stands or was left standing
    """
    waited_s: float
    orders_executed: int = 0
    orders_filled_in_part: int = 0
    orders_unaccounted: int = 0
    orders_ended_unfilled: int = 0
    orders_still_open: int = 0

    def orders_waited_for(self) -> int:
        """
        How many orders the wait waited for.

        Returns:
            The sum of the five endings
        """
        return (self.orders_executed + self.orders_filled_in_part + self.orders_unaccounted
                + self.orders_ended_unfilled + self.orders_still_open)

    @classmethod
    def over_records(
        cls,
        waited_s: float,
        keys: Iterable[AwaitedOrderKey],
        order_history: Sequence[OrderResult],
    ) -> 'StopWaitOutcome':
        """
        Derive the outcome from the order records.

        Args:
            waited_s: How long the wait ran
            keys: The orders it waited for, each by its id and its action
            order_history: The session's order records, oldest first

        Returns:
            The outcome
        """
        endings = list(endings_by_order(keys, order_history).values())
        in_part = sum(1 for ending in endings if ending.is_filled_in_part())
        filled = sum(1 for ending in endings if ending.status is OrderStatus.EXECUTED) - in_part
        unanswered = sum(1 for ending in endings if ending.status is OrderStatus.UNACCOUNTED)
        still_open = sum(1 for ending in endings if ending.status is None)
        return cls(
            waited_s=waited_s,
            orders_executed=filled,
            orders_filled_in_part=in_part,
            orders_unaccounted=unanswered,
            orders_ended_unfilled=len(endings) - filled - in_part - unanswered - still_open,
            orders_still_open=still_open,
        )
