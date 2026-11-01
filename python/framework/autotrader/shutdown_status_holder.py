"""
FiniexTestingIDE - Shutdown Status Holder (#630)

The session's one `ShutdownStatus`: the live display polls it per frame, and the journal's lines
are written from it. The session's loggers buffer their console, and a supervised session has no
terminal, so a line goes to the session log and, while no live display holds a terminal, out by a
flushed print.

No lock, and every write replaces the whole snapshot by assignment. The writers run on the main
thread, the signal handler among them, and a handler runs between two bytecodes on the very thread
that may hold a lock. Only the display reads from another thread, and it reads one attribute.
"""

import time
from dataclasses import replace
from datetime import datetime
from typing import Callable, Optional, Sequence, Tuple

from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.types.autotrader_types.shutdown_status_types import (
    ShutdownStatus,
    ShutdownStep,
)
from python.framework.types.trading_env_types.awaited_order_types import AwaitedOrder
from python.framework.types.trading_env_types.order_types import OrderStatus


class ShutdownStatusHolder:
    """
    Holds what a session's stop is doing, and says it.

    Args:
        supervisor_kill_after_s: The time the supervisor gives the process after its signal, None
            when it is not declared
    """

    # How often a wait for orders repeats in the journal what it is waiting for
    _WAIT_LINE_EVERY_S = 5.0

    def __init__(self, supervisor_kill_after_s: Optional[float]) -> None:
        self._kill_after_s = supervisor_kill_after_s
        self._status: Optional[ShutdownStatus] = None
        self._logger: Optional[AbstractLogger] = None
        self._count_orders_to_cancel: Optional[Callable[[], Optional[int]]] = None
        self._lines_on_console = True
        self._asked_is_said = False
        self._next_wait_line_s = self._WAIT_LINE_EVERY_S

    def connect_journal(
        self,
        logger: AbstractLogger,
        count_orders_to_cancel: Optional[Callable[[], Optional[int]]] = None,
    ) -> None:
        """
        Name where the lines go and who knows what the cleanup will cancel, once they exist. A
        signal can arrive before they do; its line then waits for the first step anyway.

        Args:
            logger: The session log
            count_orders_to_cancel: Answers how many resting orders the cleanup will cancel
        """
        self._logger = logger
        self._count_orders_to_cancel = count_orders_to_cancel

    def set_lines_on_console(self, on_console: bool) -> None:
        """
        Say whether the lines also go to the console, which a live display on a terminal holds for
        itself.

        Args:
            on_console: True while no live display holds a terminal
        """
        self._lines_on_console = on_console

    def get(self) -> Optional[ShutdownStatus]:
        """
        Read what the stop is doing, its ages measured against this moment.

        Returns:
            The status; None while no stop was asked for
        """
        status = self._status
        if status is None:
            return None
        return replace(status, as_of_monotonic_s=time.monotonic())

    # =========================================================================
    # WRITERS
    # =========================================================================

    def note_asked(self, asked_by: str, asked_at: datetime, asked_at_monotonic_s: float) -> None:
        """
        Keep what first asked for the stop. Called from a signal handler, so it does nothing but
        assign: a buffered print there can raise, on a write the handler interrupted. The first step
        says the line.

        Args:
            asked_by: What asked, in a word or two
            asked_at: When, UTC
            asked_at_monotonic_s: The same instant on the monotonic clock
        """
        if self._status is not None:
            return
        self._status = ShutdownStatus(
            asked_by=asked_by,
            asked_at=asked_at,
            as_of_monotonic_s=asked_at_monotonic_s,
            kill_at_monotonic_s=(asked_at_monotonic_s + self._kill_after_s
                                 if self._kill_after_s is not None else None),
        )

    def enter_step(self, step: ShutdownStep) -> None:
        """
        A step of the stop begins. A step after the loop also ends what a wait left behind: a wait
        that crashed reached no end of its own, and its orders would stay on screen as awaited.

        Args:
            step: The step
        """
        self._say_asked_once()
        if self._status is None:
            return
        self._status = replace(
            self._status,
            step=step,
            orders_to_cancel=self._read_orders_to_cancel(),
            awaited_orders=(),
            order_wait_began_monotonic_s=None,
            order_wait_bound_s=None,
        )
        self._say(self._status.step_line())

    def say(self, line: str) -> None:
        """
        Say a line of the stop no step stands for.

        Args:
            line: The line
        """
        self._say_asked_once()
        self._say(line)

    def note_tick_source_stopped(self) -> None:
        """The session stops its tick source now, so that its silence is no fault from here on."""
        if self._status is not None:
            self._status = replace(self._status, tick_source_stopped=True)

    def order_wait_began(self, awaited: Sequence[AwaitedOrder], began_monotonic_s: float,
                         bound_s: float) -> None:
        """
        The stop begins to wait for the orders on their way.

        Args:
            awaited: What it waits for
            began_monotonic_s: `time.monotonic()` when the wait began
            bound_s: How long it may run
        """
        self._say_asked_once()
        if self._status is None:
            return
        self._status = replace(
            self._status,
            awaited_orders=tuple(awaited),
            order_wait_began_monotonic_s=began_monotonic_s,
            order_wait_bound_s=bound_s,
            orders_waited_for=frozenset(row.order_id for row in awaited),
            orders_to_cancel=self._read_orders_to_cancel(),
        )
        self._say(f'⏳ Stop: {len(awaited)} order(s) on their way, waiting up to {bound_s:.1f} s')

    def order_wait_passed(
        self,
        awaited: Sequence[AwaitedOrder],
        settled: Sequence[Tuple[AwaitedOrder, Optional[OrderStatus]]],
        waited_s: float,
        bound_s: float,
    ) -> None:
        """
        A pass of the wait is over: one line per order it settled, and what is left when that
        changed or when five seconds have gone by without a word.

        Args:
            awaited: What is still on its way
            settled: Each order no longer on its way, with its ending; None for an order with none
                on the record yet, as a resting order the venue took
            waited_s: How long the wait has run
            bound_s: How much longer it may run
        """
        status = self._status
        if status is None:
            return
        current = replace(
            status,
            awaited_orders=tuple(awaited),
            order_wait_bound_s=waited_s + bound_s,
            orders_waited_for=status.orders_waited_for | {row.order_id for row in awaited},
            orders_to_cancel=self._read_orders_to_cancel(),
        )
        self._status = current
        for row, ending in settled:
            how = ending.value if ending is not None else 'open at the venue'
            self._say(f'⏳ Stop: {row.describe()} settled after {waited_s:.1f} s: {how}')
        if not awaited:
            self._say(f'⏳ Stop: nothing on its way any more after {waited_s:.1f} s')
            return
        if len(awaited) != len(status.awaited_orders):
            self._say(f'⏳ Stop: {len(awaited)} order(s) still on their way after '
                      f'{waited_s:.1f} s, at most {bound_s:.1f} s more')
        if waited_s >= self._next_wait_line_s:
            self._next_wait_line_s = waited_s + self._WAIT_LINE_EVERY_S
            reading = replace(current, as_of_monotonic_s=time.monotonic())
            for line in reading.order_lines():
                self._say(f'⏳ Stop: waited {waited_s:.1f} s of {waited_s + bound_s:.0f} s · {line}')

    def order_wait_ran_out(self, awaited: Sequence[AwaitedOrder], waited_s: float) -> None:
        """
        The bound of the wait ran out. Said before the last read, which can take a while.

        Args:
            awaited: What is still on its way
            waited_s: How long the wait ran
        """
        self._say(f'⏳ Stop: the wait ran out after {waited_s:.1f} s with {len(awaited)} order(s) '
                  f'on their way')

    def order_wait_given_up(self, given_up: Sequence[AwaitedOrder],
                            left: Sequence[AwaitedOrder], waited_s: float) -> None:
        """
        What a wait that ran out came to.

        Args:
            given_up: The orders recorded unaccounted
            left: What is still on its way, for the cleanup to take
            waited_s: How long the wait ran
        """
        self._say(f'⏳ Stop: {len(given_up)} order(s) recorded unaccounted, {len(left)} left to the '
                  f'cleanup')
        self.order_wait_ended(waited_s)

    def order_wait_ended(self, ran_s: float) -> None:
        """
        The wait is over, however it ended.

        Args:
            ran_s: How long it ran
        """
        if self._status is None:
            return
        self._status = replace(
            self._status,
            awaited_orders=(),
            order_wait_began_monotonic_s=None,
            order_wait_bound_s=None,
            order_wait_ran_s=ran_s,
        )

    # =========================================================================
    # THE LINES
    # =========================================================================

    def _read_orders_to_cancel(self) -> Optional[int]:
        """
        Ask how many resting orders the cleanup will cancel.

        Returns:
            The count; None where nobody can say
        """
        if self._count_orders_to_cancel is None:
            return None
        try:
            return self._count_orders_to_cancel()
        except Exception as error:
            # A number on a status panel must never end the stop before its cancels and its records
            self._count_orders_to_cancel = None
            if self._logger is not None:
                self._logger.error(f'The stop could not count the orders to cancel: {error}')
            return None

    def _say_asked_once(self) -> None:
        """Say what asked for the stop, at the first step after it, and only then."""
        status = self.get()
        if status is None or self._asked_is_said:
            return
        self._asked_is_said = True
        kill = status.kill_text()
        self._say(f'{status.headline()} · kill {kill}' if kill else status.headline())

    def _say(self, line: str) -> None:
        """
        Write one line to the session log and, where nothing else holds the console, out at once.

        A console that is gone must not end a stop, and the session log still has the line: a
        closed stream, a broken pipe to a journal, or a write a signal handler's own print
        interrupted.

        Args:
            line: The line
        """
        if self._logger is not None:
            self._logger.info(line)
        if not self._lines_on_console:
            return
        try:
            print(line, flush=True)
        except (OSError, ValueError, RuntimeError):
            pass
