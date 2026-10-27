"""
FiniexTestingIDE - Run Stream Sealer (#476)

The open streams of one session, sealed together at each trading-day boundary. Every stream the
session's writer opens is registered here, so the boundary reaches all of them at once — the order
events, the trades, the state frames, the tick stream record and each diagnostics sink — and each
one's day moves into its day record in the same pass.

The sealer also knows which trading day the session is on once it has sealed one, so a stream
opened later names that day in its header. A stream opened before the first seal names none; the
seal then labels its file with the day it ended.
"""

import time
from datetime import date
from pathlib import Path
from typing import Callable, FrozenSet, List, Optional, Set, Tuple

from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.reporting.io.run_stream_appender import RunStreamAppender
from python.framework.types.store_member_types import RunEntryMemberId


class RunStreamSealer:
    """
    Seals every registered stream of a session at a trading-day boundary.

    Args:
        logger: The session's channel, where each seal is said
    """

    def __init__(self, logger: AbstractLogger):
        self._logger = logger
        self._streams: List[Tuple[RunStreamAppender, Callable[[date], Path], str,
                                  RunEntryMemberId]] = []
        self._current_day: Optional[date] = None
        self._due: Set[RunEntryMemberId] = set()

    def register(self, appender: RunStreamAppender, day_path_for: Callable[[date], Path],
                 label: str, day_member: RunEntryMemberId) -> None:
        """
        Seal a stream with the others from now on.

        Args:
            appender: The open stream
            day_path_for: Where the stream's day record of a trading day belongs
            label: What the seal's line calls the stream
            day_member: The member its day records are
        """
        self._streams.append((appender, day_path_for, label, day_member))

    def seal(self, sealed_day: date, next_day: date) -> None:
        """
        Seal every registered stream's day and start each one afresh for the next day. A stream
        that cannot be sealed says so itself; the others are sealed regardless.

        Args:
            sealed_day: The trading day the boundary ended
            next_day: The trading day the boundary opened
        """
        started = time.perf_counter()
        sealed: List[str] = []
        for appender, day_path_for, label, day_member in self._streams:
            self._due.add(day_member)
            summary = appender.seal_day(day_path_for, sealed_day, next_day)
            if summary is not None:
                sealed.append(f'{label} {summary.record_count:,}')
        self._current_day = next_day
        duration_s = time.perf_counter() - started
        listed = ' · '.join(sealed) if sealed else 'no stream was open'
        self._logger.info(f'🗄️ Day records sealed for {sealed_day} — {listed} ({duration_s:.2f} s)')

    def get_current_day(self) -> Optional[date]:
        """
        The trading day the session is on, once it sealed one.

        Returns:
            The day, or None before the first seal
        """
        return self._current_day

    def get_due_day_members(self) -> FrozenSet[RunEntryMemberId]:
        """
        The day records a seal was due for: the members of every stream that was open when a
        trading day ended — what the session's footprint expects, written or not.

        Returns:
            The day-record members
        """
        return frozenset(self._due)
