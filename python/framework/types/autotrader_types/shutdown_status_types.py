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
from python.framework.types.trading_env_types.order_types import OrderResult, OrderStatus


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
        kill_at_monotonic_s: When the declared kill falls; None when no kill is declared
        tick_source_stopped: Whether the session stopped its tick source, so that a stream gone
            silent is no fault
        orders_to_cancel: How many resting orders the cleanup will cancel; None while no executor
            can say
        awaited_orders: What the stop waits for at this moment
        order_wait_began_monotonic_s: When the wait for them began; None while no wait runs
        order_wait_bound_s: How long that wait may run in all, as its last pass read it; None while
            no wait runs
        orders_waited_for: The id of every order a wait waited for, which outlasts the wait
        order_wait_ran_s: How long the wait ran; None while it runs, and when there was none
    """
    asked_by: str
    asked_at: datetime
    as_of_monotonic_s: float
    step: ShutdownStep = ShutdownStep.LOOP_END
    kill_at_monotonic_s: Optional[float] = None
    tick_source_stopped: bool = False
    orders_to_cancel: Optional[int] = None
    awaited_orders: Tuple[AwaitedOrder, ...] = ()
    order_wait_began_monotonic_s: Optional[float] = None
    order_wait_bound_s: Optional[float] = None
    orders_waited_for: FrozenSet[str] = frozenset()
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
            For instance `4.2 s of 30 s, then: booked unaccounted, reported, session ends itself`;
            None while no wait runs
        """
        began = self.order_wait_began_monotonic_s
        if began is None or self.order_wait_bound_s is None:
            return None
        return (f'{self.as_of_monotonic_s - began:.1f} s of {self.order_wait_bound_s:.0f} s, '
                f'then: booked unaccounted, reported, session ends itself')

    def kill_text(self) -> Optional[str]:
        """
        How long until the supervisor's declared kill.

        Returns:
            For instance `in 75.8 s (supervisor)`, `in 0.0 s` once it is due; None when no kill is
            declared
        """
        if self.kill_at_monotonic_s is None:
            return None
        until_kill_s = max(0.0, self.kill_at_monotonic_s - self.as_of_monotonic_s)
        return f'in {until_kill_s:.1f} s (supervisor)'


def last_status_by_order(
    order_ids: Iterable[str],
    order_history: Sequence[OrderResult],
) -> Dict[str, Optional[OrderStatus]]:
    """
    How each of some orders ended, read off the session's order records.

    Args:
        order_ids: The orders
        order_history: The records, oldest first

    Returns:
        Each order's last ending; None for an order with no ending on the record, as a resting
        order the venue took has none
    """
    endings: Dict[str, Optional[OrderStatus]] = dict.fromkeys(order_ids)
    for row in reversed(order_history):
        if (row.order_id in endings and endings[row.order_id] is None
                and row.status is not OrderStatus.PENDING):
            endings[row.order_id] = row.status
    return endings


@dataclass(frozen=True)
class StopWaitOutcome:
    """
    What a stop's wait for the orders on their way came to (#630).

    Read off the order records when the session's result is collected, so that it reports how each
    order ended, after the cleanup as well, and no counter has to be subtracted from another.

    Args:
        waited_s: How long the wait ran, on the monotonic clock
        orders_executed: Waited for, and `executed`: fully or in part, and booked
        orders_unaccounted: Waited for, and `unaccounted`: the venue did not say
        orders_ended_unfilled: Waited for, and any other ending: refused, cancelled, expired
        orders_still_open: Waited for, and with no ending on the record: a resting order the venue
            took, which stands or was left standing
    """
    waited_s: float
    orders_executed: int = 0
    orders_unaccounted: int = 0
    orders_ended_unfilled: int = 0
    orders_still_open: int = 0

    def orders_waited_for(self) -> int:
        """
        How many orders the wait waited for.

        Returns:
            The sum of the four endings
        """
        return (self.orders_executed + self.orders_unaccounted + self.orders_ended_unfilled
                + self.orders_still_open)

    @classmethod
    def over_records(
        cls,
        waited_s: float,
        order_ids: Iterable[str],
        order_history: Sequence[OrderResult],
    ) -> 'StopWaitOutcome':
        """
        Derive the outcome from the order records.

        Args:
            waited_s: How long the wait ran
            order_ids: The orders it waited for
            order_history: The session's order records, oldest first

        Returns:
            The outcome
        """
        endings = list(last_status_by_order(order_ids, order_history).values())
        filled = endings.count(OrderStatus.EXECUTED)
        unanswered = endings.count(OrderStatus.UNACCOUNTED)
        still_open = endings.count(None)
        return cls(
            waited_s=waited_s,
            orders_executed=filled,
            orders_unaccounted=unanswered,
            orders_ended_unfilled=len(endings) - filled - unanswered - still_open,
            orders_still_open=still_open,
        )
