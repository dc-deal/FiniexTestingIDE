"""
FiniexTestingIDE - Tick Stream Recorder
The live tick source's account of its own connection (#609): every outage with the public trade
ids around it, and the trades it refused — the first of each reason listed, every one counted —
one line each in the session's `io/tick_stream_events.jsonl`, written through the run stream
appender like every stream a run writes as it goes.

Transport health, and kept on the transport plane: the ids never ride on a tick, so no worker can
reach them and the backtest needs nothing of it. The comparison of a run (#512) reads this record
beside the archive's own trade ids, and fetches what the session missed from the venue after the
run — a live session never backfills.

Every method runs on the tick source's thread, the file's only writer; the constructor runs before
that thread starts.
"""

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.reporting.io.run_stream_appender import RunStreamAppender
from python.framework.types.autotrader_types.tick_stream_event_types import (
    RefusedPublicTrade,
    RefusedPublicTradeReason,
    RefusedPublicTradeTotals,
    TickStreamLossCause,
    TickStreamOutage,
)
from python.framework.types.log_layout_types import (
    TICK_STREAM_EVENTS_FILE,
    TICK_STREAM_EVENTS_SCHEMA_VERSION,
    TICK_STREAM_EVENTS_STREAM,
)

# How much of a refused value is kept: enough to recognise the frame, never the whole frame.
_RAW_EXCERPT_CHARS = 120
# How many refusals of one reason are listed; the rest are counted, and the totals close the
# record. A venue that changed its format would otherwise turn every trade into a line, for as long
# as the session runs.
REFUSALS_LISTED_PER_REASON = 100


def _utc_now() -> datetime:
    """
    The local wall clock as an aware UTC instant — the time we noticed, never an event time.

    Returns:
        The current instant in UTC
    """
    return datetime.now(timezone.utc)


class TickStreamRecorder:
    """
    Keeps the record of one live tick stream's outages and refused trades.

    Args:
        symbol: The session's symbol
        record_dir: The run's `io/` directory, or None when the session keeps no record
        run_id: Written into the record's header
        session_logger: Where the operator reads a refusal and a failed write; None for nowhere
        wall_clock: Reads the instant of an observation
        monotonic_clock: Reads the clock durations are measured on
    """

    def __init__(
        self,
        symbol: str,
        record_dir: Optional[Path] = None,
        run_id: str = '',
        session_logger: Optional[AbstractLogger] = None,
        wall_clock: Callable[[], datetime] = _utc_now,
        monotonic_clock: Callable[[], float] = time.monotonic,
    ):
        self._symbol = symbol
        self._session_logger = session_logger
        self._wall_clock = wall_clock
        self._monotonic_clock = monotonic_clock

        self._last_trade_id: Optional[int] = None
        self._last_trade_time: Optional[datetime] = None
        self._open_outage: Optional[TickStreamOutage] = None
        self._lost_monotonic: float = 0.0
        # The subscription of the connection that is up now, while an outage is open: copied
        # into the outage only when that connection is still the one delivering — a connection
        # lost again before any trade must leave no restoration behind.
        self._pending_restore: Optional[Tuple[datetime, float]] = None
        self._closed = False
        self._outages_started: int = 0
        # Read by the tick loop on the main thread: one int, so a reader sees the old count or
        # the new one and never a mixture.
        self._outages_ended: int = 0
        self._refusals: Dict[RefusedPublicTradeReason, int] = {}

        # A header line, one flushed line per record, and the first failed write said once on the
        # session's channel — it never reaches the socket loop, where it would read as a broken
        # connection.
        self._stream: Optional[RunStreamAppender] = None
        if record_dir is not None:
            self._stream = RunStreamAppender(
                path=Path(record_dir) / TICK_STREAM_EVENTS_FILE,
                stream=TICK_STREAM_EVENTS_STREAM,
                schema_version=TICK_STREAM_EVENTS_SCHEMA_VERSION,
                run_id=run_id,
                logger=session_logger,
                stops='Outages and refused trades are no longer recorded this session',
                header_fields={'symbol': symbol},
            )

    # === What the tick source reports ===

    def on_connection_lost(self, cause: TickStreamLossCause, delivered_for_s: float) -> None:
        """
        A connection that had subscribed is gone. Opens an outage, unless one is already open —
        a connection that came back and was lost again before any trade arrived is the same
        outage, and its first loss is the one it reports.

        Args:
            cause: How the loss was detected
            delivered_for_s: How long the lost connection delivered, on the monotonic clock
        """
        if self._open_outage is not None:
            # The connection that came back was lost again: its restoration is void.
            self._pending_restore = None
            return
        self._outages_started += 1
        self._lost_monotonic = self._monotonic_clock()
        self._open_outage = TickStreamOutage(
            outage_no=self._outages_started,
            symbol=self._symbol,
            lost_at=self._wall_clock(),
            detected_by=cause,
            delivered_for_s=delivered_for_s,
            last_public_trade_id=self._last_trade_id,
            last_public_trade_time=self._last_trade_time,
        )

    def on_attempt(self) -> None:
        """A connection attempt starts; it counts toward the open outage, if there is one."""
        if self._open_outage is not None:
            self._open_outage.attempts += 1

    def on_restored(self) -> None:
        """
        A connection subscribed again. It counts as the restoration once it delivers a trade, or
        if it is still up at stop.
        """
        if self._open_outage is None:
            return
        self._pending_restore = (self._wall_clock(), self._monotonic_clock())

    def on_trade(self, public_trade_id: Optional[int], trade_time: datetime) -> None:
        """
        A trade became a tick. The first one after a loss ends the outage.

        Args:
            public_trade_id: The venue's id of the trade, or None when it sent none
            trade_time: The trade's venue time
        """
        if self._open_outage is not None:
            self._open_outage.first_public_trade_id = public_trade_id
            self._open_outage.first_public_trade_time = trade_time
            self._finish_outage()
            self._outages_ended += 1
        self._last_trade_id = public_trade_id
        self._last_trade_time = trade_time

    def on_refused_trade(
        self,
        reason: RefusedPublicTradeReason,
        public_trade_id: Optional[int],
        raw: str,
    ) -> None:
        """
        A trade from the venue was refused instead of becoming a tick: counted, listed while its
        reason has fewer than `REFUSALS_LISTED_PER_REASON` lines, and the first refusal of each
        kind is said in the session log.

        Args:
            reason: What was wrong with it
            public_trade_id: Its id when the entry carried a readable one
            raw: The offending value as received
        """
        excerpt = raw[:_RAW_EXCERPT_CHARS]
        count = self._refusals.get(reason, 0) + 1
        self._refusals[reason] = count
        if count <= REFUSALS_LISTED_PER_REASON:
            self._write(RefusedPublicTrade(
                symbol=self._symbol,
                received_at=self._wall_clock(),
                reason=reason,
                raw=excerpt,
                public_trade_id=public_trade_id,
            ).to_record())
        if count == 1 and self._session_logger is not None:
            trade_id = public_trade_id if public_trade_id is not None else 'unknown'
            self._session_logger.warning(
                f'📡 {self._symbol}: a trade from the venue was refused — {reason.describe()} '
                f"(public trade id {trade_id}, value '{excerpt}'). It is not used. "
                f'{TICK_STREAM_EVENTS_FILE} lists the first {REFUSALS_LISTED_PER_REASON} '
                f'refusals of each kind and counts every one; further refusals of this kind are '
                f'not logged here.')

    # === Reads ===

    def get_outages_ended(self) -> int:
        """
        Outages the stream came back from — a trade arrived after each. Safe to read from the
        tick loop's thread.

        Returns:
            The count since the session started
        """
        return self._outages_ended

    def get_refusal_count(self) -> int:
        """
        Trades refused this session.

        Returns:
            The count over every reason
        """
        return sum(self._refusals.values())

    # === End ===

    def close(self) -> None:
        """
        Finish the record: an outage still open is written with what is known, and refusals are
        summed up — as the record's closing line and once in the session log. A second call does
        nothing.
        """
        if self._closed:
            return
        self._closed = True
        if self._open_outage is not None:
            self._finish_outage()
        total = self.get_refusal_count()
        if total:
            self._write(RefusedPublicTradeTotals(
                symbol=self._symbol,
                totals=dict(self._refusals),
                listed_per_reason=REFUSALS_LISTED_PER_REASON,
            ).to_record())
        if total and self._session_logger is not None:
            breakdown = ', '.join(f'{reason.value} {count}'
                                  for reason, count in self._refusals.items())
            self._session_logger.warning(
                f'📡 {self._symbol}: {total} trade(s) from the venue refused this session '
                f'({breakdown}) — {TICK_STREAM_EVENTS_FILE} lists the first '
                f'{REFUSALS_LISTED_PER_REASON} of each kind, and its last line the totals.')
        if self._stream is not None:
            self._stream.close()

    # === Internals ===

    def _finish_outage(self) -> None:
        """
        Write the open outage and forget it. Its restoration is the connection that is up now —
        the one that delivered the first trade, or the one still up at stop — never one lost
        again before any trade.
        """
        if self._pending_restore is not None:
            restored_at, restored_monotonic = self._pending_restore
            self._open_outage.restored_at = restored_at
            self._open_outage.outage_s = restored_monotonic - self._lost_monotonic
        self._write(self._open_outage.to_record())
        self._open_outage = None
        self._pending_restore = None

    def _write(self, record: Dict[str, object]) -> None:
        """
        Append one line to the record, when the session keeps one.

        Args:
            record: The line's values
        """
        if self._stream is not None:
            self._stream.write(record)
