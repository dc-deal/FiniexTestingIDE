"""
FiniexTestingIDE - The Money Record's Streams (#632)

The order events and the trades are a session's money record: what its money figures are derived
from, and what the parity proof compares with the backtest over the same period. While either cannot
be written, the session holds its entries — #499's gate, reason `record_unwritable`: it manages and
closes what it has, and opens nothing it could not record. The streams keep the lines they could not
write in memory, in their order, and write them again at the record's cadence; the hold lifts the
moment every kept line is on the disk. While they write, the same cadence forces their lines to the
disk, so a stop of the machine loses at most that much of the record.
"""

from typing import Callable, List, Optional, Tuple

from python.framework.autotrader.operator_alerts.operator_alerts import OperatorAlerts
from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.reporting.io.run_stream_appender import RunStreamAppender
from python.framework.trading_env.live.live_trade_executor import LiveTradeExecutor
from python.framework.types.autotrader_types.operator_alert_types import (
    OperatorAlertKind,
    OperatorAlertLevel,
)
from python.framework.types.trading_env_types.entry_hold_types import EntryHoldLift, EntryHoldReason


class MoneyRecordStreams:
    """
    The session's money-record streams, forced to the disk at a cadence, and the entry hold over
    them while one cannot be written.

    Args:
        executor: The session's executor, whose entries are held and whose clock dates the hold
        operator_alerts: Where the hold and its lifting are reported out of band; None without one
        logger: The session logger
        sync_interval_s: The cadence (`autotrader.storage.stream_sync_interval_s`)
    """

    def __init__(self, executor: LiveTradeExecutor, operator_alerts: Optional[OperatorAlerts],
                 logger: AbstractLogger, sync_interval_s: float):
        self._executor = executor
        self._operator_alerts = operator_alerts
        self._logger = logger
        self._interval_s = sync_interval_s
        self._streams: List[Tuple[str, RunStreamAppender]] = []

    def failure_listener(self, stream: str) -> Callable[[OSError], None]:
        """
        What a money-record stream tells the first failure of each stretch it cannot be written.

        Args:
            stream: The stream's name, as the operator is told

        Returns:
            The listener, which holds the session's entries
        """
        def on_failure(error: OSError) -> None:
            """
            Hold the entries over a stream that could not be written.

            Args:
                error: What the file system answered
            """
            self._hold(stream, error)
        return on_failure

    def add(self, stream: str, appender: RunStreamAppender) -> None:
        """
        Keep one stream of the money record on the disk.

        Args:
            stream: Its name, as the operator is told
            appender: The open stream, opened to keep what it could not write
        """
        self._streams.append((stream, appender))

    def get_interval_s(self) -> float:
        """
        How often the streams are forced to the disk, or written again.

        Returns:
            Seconds between two runs of `sync`
        """
        return self._interval_s

    def sync(self) -> None:
        """
        Force every stream's new lines to the disk, write again what a failing one kept, and lift
        the hold once nothing is kept any more.
        """
        kept = sum(appender.get_unwritten_count() for _, appender in self._streams)
        for _, appender in self._streams:
            appender.sync()
        holds = self._executor.get_entry_holds()
        if not holds.is_held(EntryHoldReason.RECORD_UNWRITABLE):
            return
        if any(appender.is_failing() for _, appender in self._streams):
            return
        holds.lift(EntryHoldReason.RECORD_UNWRITABLE, EntryHoldLift.OBSERVED,
                   self._executor.get_current_time_if_set())
        self._logger.info(
            f'✅ Entries lifted: the money record is written again — the {kept} line(s) it kept in '
            f'memory are on the disk.')
        self._send(OperatorAlertKind.ENTRIES_LIFTED, OperatorAlertLevel.INFO,
                   'Entries lifted — the money record is written again',
                   f'The {kept} line(s) the session kept in memory are on the disk; it opens '
                   f'positions again.')

    def _hold(self, stream: str, error: OSError) -> None:
        """
        Hold the entries over a stream that could not be written — once while the hold stands.

        Args:
            stream: The stream that failed
            error: What the file system answered
        """
        detail = (f'the {stream} stream cannot be written ({type(error).__name__}: {error}) — '
                  f'its lines are kept in memory and written again every {self._interval_s:g} s; '
                  f'the hold lifts when they are on the disk')
        placed = self._executor.get_entry_holds().hold(
            EntryHoldReason.RECORD_UNWRITABLE, self._executor.get_current_time_if_set(), detail)
        if not placed:
            return
        self._send(OperatorAlertKind.ENTRIES_HELD, OperatorAlertLevel.CRITICAL,
                   'Entries held — the money record cannot be written',
                   f'{detail[0].upper()}{detail[1:]}.\nThe session runs: it manages and closes its '
                   f'position and opens nothing new. Free the disk or fix the run directory.')

    def _send(self, kind: OperatorAlertKind, level: OperatorAlertLevel, title: str,
              detail: str) -> None:
        """
        Report out of band, when the session has a channel.

        Args:
            kind: Which moment
            level: How urgent
            title: The preview line
            detail: The further lines
        """
        if self._operator_alerts is not None:
            self._operator_alerts.send(kind, level, title, detail)
