"""
FiniexTestingIDE - Warmup History (#588)

The bars a session's workers warm up on, owned by one component in both pipelines — from the
source that supplied them to the first tick that ends the warmup.

The rule every path selects by: the history at the first tick T holds the last N bars COMPLETE
at T — bars whose period had ended by then (bar_period). Only who selects differs:
  - a backtest and a mock session read the archive, and the batch selects their history for the
    scenario's first tick before any process starts, so the package arrives already closed;
  - a live-adapter session reads the venue at boot, before its first tick exists, so it selects
    for the instant of that read — and when the first tick arrives, every timeframe whose period
    ended in between is read again for the tick, before the tick is rendered (the seam).
After the seam every path holds the same history for the same data, which is what lets a
backtest of a live session's window start from the state the session started from.

Either way the bars enter the renderer through one checked route,
BarRenderingController.inject_warmup_bars, so a selection defect stops the run on every path
instead of reaching the workers on one of them. A session records the history it started from
in its run directory (`io/warmup_history.json`): a live session's warmup comes from the venue and
exists nowhere else afterwards.
"""

import json
from dataclasses import asdict
from datetime import datetime
from typing import Callable, Dict, List, Optional

from python.framework.bars.bar_rendering_controller import BarRenderingController
from python.framework.exceptions.warmup_history_errors import WarmupHistoryRefusedError
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.types.connection_types import ConnectionOutcome
from python.framework.types.market_types.market_data_types import Bar
from python.framework.types.market_types.warmup_history_types import (
    WarmupHistoryRecord,
    WarmupSource,
    WarmupTimeframeRecord,
)
from python.framework.store.entry_members.run_entry_writer import RunEntryWriter
from python.framework.types.process_data_types import ProcessDataPackage
from python.framework.types.store_member_types import RunEntryMemberId
from python.framework.utils.bar_period import open_time_of, period_end, period_start
from python.framework.utils.connection_ladder import ConnectionLadder, run_with_ladder
from python.framework.utils.process_serialization_utils import deserialize_bars_batch

# (timeframe, count, reference instant) -> the newest `count` bars complete at that instant.
# Raises when the venue cannot be read.
VenueBarFetch = Callable[[str, int, datetime], List[Bar]]


class WarmupHistory:
    """
    One session's warmup history: its bars, the instant they were selected for, their source.

    Built by from_package (archive) or from_venue (live adapter), then handed to the renderer
    with inject_into, which also arranges for the seam to close at the first tick.

    Args:
        symbol: Trading symbol from config (authoritative)
        bars_by_timeframe: The bars, oldest first, by timeframe
        reference_time: The instant they were selected for; None when there are none
        source: Where they were read from
        logger: ScenarioLogger for status messages
        fetch: The venue read, for closing the seam; None for the archive, whose history was
            selected for the first tick already
        required_by_timeframe: Bars the workers require, by timeframe — what a re-read asks for
        unreadable_timeframes: Timeframes the venue could not be read for at boot
        unreadable_for_now: Those of them whose every attempt failed on a passing fault — a
            timeout, a 5xx, a 429 — so a later start may read them (#598)
    """

    def __init__(
        self,
        symbol: str,
        bars_by_timeframe: Dict[str, List[Bar]],
        reference_time: Optional[datetime],
        source: WarmupSource,
        logger: ScenarioLogger,
        fetch: Optional[VenueBarFetch] = None,
        required_by_timeframe: Optional[Dict[str, int]] = None,
        unreadable_timeframes: Optional[List[str]] = None,
        unreadable_for_now: Optional[List[str]] = None,
    ):
        self._symbol = symbol
        self._bars_by_timeframe = bars_by_timeframe
        self._reference_time = reference_time
        self._source = source
        self._logger = logger
        self._fetch = fetch
        self._required_by_timeframe = required_by_timeframe or {}
        self._unreadable_timeframes = unreadable_timeframes or []
        self._unreadable_for_now = unreadable_for_now or []
        self._read_at = reference_time
        self._first_tick_time: Optional[datetime] = None
        self._reread_at_first_tick: List[str] = []
        self._run_writer: Optional[RunEntryWriter] = None

    @classmethod
    def from_package(
        cls,
        symbol: str,
        package: Optional[ProcessDataPackage],
        logger: ScenarioLogger,
        required_by_timeframe: Optional[Dict[str, int]] = None,
    ) -> 'WarmupHistory':
        """
        The history the batch selected from the archive for the scenario's first tick.

        Args:
            symbol: Trading symbol from config (authoritative)
            package: The scenario's prepared data; None yields an empty history
            logger: ScenarioLogger for status messages
            required_by_timeframe: Bars the workers require, by timeframe — for the record

        Returns:
            The archive history, already closed at the first tick
        """
        if package is None:
            return cls(symbol, {}, None, WarmupSource.ARCHIVE, logger)
        bars = {
            timeframe: list(deserialize_bars_batch(symbol, rows))
            for timeframe, rows in package.bars.items()
        }
        return cls(symbol, bars, package.warmup_reference_time, WarmupSource.ARCHIVE, logger,
                   required_by_timeframe=required_by_timeframe)

    @classmethod
    def from_venue(
        cls,
        symbol: str,
        required_by_timeframe: Dict[str, int],
        fetch: VenueBarFetch,
        ladder: ConnectionLadder,
        reference_time: datetime,
        logger: ScenarioLogger,
    ) -> 'WarmupHistory':
        """
        Read the history from the venue at boot, selected for the instant of the read.

        The boot read retries under the ladder. A timeframe the ladder gave up on is kept as
        an empty history and named in get_unreadable_timeframes, so the caller can refuse
        saying which timeframe failed rather than that "the endpoint did not answer".

        Args:
            symbol: Trading symbol from config (authoritative)
            required_by_timeframe: Bars the workers require, by timeframe
            fetch: The venue read
            ladder: Retry policy for the boot read
            reference_time: The instant the history is selected for — when the read is made
            logger: ScenarioLogger for status messages

        Returns:
            The venue history, its seam still to close at the first tick
        """
        bars: Dict[str, List[Bar]] = {}
        unreadable: List[str] = []
        unreadable_for_now: List[str] = []
        for timeframe, count in required_by_timeframe.items():
            failures: List[BaseException] = []

            def attempt(tf: str = timeframe, n: int = count,
                        seen: List[BaseException] = failures) -> List[Bar]:
                try:
                    return fetch(tf, n, reference_time)
                except Exception as error:
                    seen.append(error)
                    raise

            fetched = run_with_ladder(attempt, ladder)
            if fetched is None:
                unreadable.append(timeframe)
                # #598: given up on a passing fault, a later start may read it
                if failures and ladder.classify(failures[-1]) is ConnectionOutcome.TRANSIENT:
                    unreadable_for_now.append(timeframe)
            bars[timeframe] = fetched or []
            logger.debug(
                f'  📊 {timeframe}: {len(bars[timeframe])}/{count} bars read from the venue')
        return cls(
            symbol, bars, reference_time, WarmupSource.VENUE, logger,
            fetch=fetch, required_by_timeframe=required_by_timeframe,
            unreadable_timeframes=unreadable, unreadable_for_now=unreadable_for_now)

    def get_bars_by_timeframe(self) -> Dict[str, List[Bar]]:
        """
        The history's bars, oldest first, by timeframe.

        Returns:
            Dict[timeframe, List[Bar]]
        """
        return self._bars_by_timeframe

    def get_available_by_timeframe(self) -> Dict[str, int]:
        """
        How many bars the history holds, by timeframe.

        Returns:
            Dict[timeframe, count]
        """
        return {timeframe: len(bars) for timeframe, bars in self._bars_by_timeframe.items()}

    def get_reference_time(self) -> Optional[datetime]:
        """
        The instant the history was selected for (updated when the seam re-reads a timeframe).

        Returns:
            The instant, or None for an empty history
        """
        return self._reference_time

    def get_source(self) -> WarmupSource:
        """
        Where the history was read from.

        Returns:
            WarmupSource
        """
        return self._source

    def get_unreadable_timeframes(self) -> List[str]:
        """
        Timeframes the venue could not be read for at boot.

        Returns:
            Timeframe names; empty for the archive
        """
        return self._unreadable_timeframes

    def is_unreadable_only_for_now(self) -> bool:
        """
        Whether every timeframe the venue could not be read for failed on a passing fault.

        Returns:
            True when something is unreadable and all of it only for now; False otherwise
        """
        return (bool(self._unreadable_timeframes)
                and set(self._unreadable_timeframes) <= set(self._unreadable_for_now))

    def inject_into(
        self,
        bar_controller: BarRenderingController,
        run_writer: Optional[RunEntryWriter] = None,
    ) -> None:
        """
        Hand the history to the renderer through its checked route, and arm the seam.

        Args:
            bar_controller: The session's bar rendering controller
            run_writer: The session's writer — the history is recorded as its warmup history,
                now and again once the seam has closed. None records nothing

        Returns:
            None
        """
        if self._reference_time is None or not self._bars_by_timeframe:
            self._logger.debug('⏭️  No warmup history to inject')
            return
        self._run_writer = run_writer

        bar_controller.inject_warmup_bars(
            self._symbol, self._bars_by_timeframe, self._reference_time)
        bar_controller.register_warmup_seam(
            lambda first_tick_time: self._close_seam(first_tick_time, bar_controller))

        self._logger.info(
            f"✅ Warmup injected: {sum(self.get_available_by_timeframe().values())} bars "
            f"({', '.join(f'{tf}:{len(bars)}' for tf, bars in self._bars_by_timeframe.items())}) "
            f"from the {self._source.value}, complete at {self._reference_time.isoformat()}"
        )
        self._log_tails()
        self._write_record()

    def _close_seam(
        self, first_tick_time: datetime, bar_controller: BarRenderingController
    ) -> None:
        """
        At the first tick, re-read every timeframe whose period ended since the boot read.

        Runs once, before the first tick is rendered. The archive's history was selected for
        this very tick and has nothing to close. The venue's was selected at boot: where its
        newest bar ends before the first tick's period opens, the periods in between ended
        after the read, and the timeframe is read again for the tick and replaced whole — so
        the history becomes the last N bars complete at the first tick, as in a backtest.

        A single attempt, never a retry with a wait: this runs inside the tick loop. A
        re-read that fails, or that still lacks the bar because the venue has not committed
        it yet (Kraken committed a period 0.8 s after its end on 2026-10-12 and up to 2.2 s
        after it on 2026-10-13), is a
        refusal — no decision has run yet, so stopping here is stopping before trading.

        Args:
            first_tick_time: The first tick's timestamp
            bar_controller: The session's bar rendering controller

        Returns:
            None
        """
        self._first_tick_time = first_tick_time
        if self._fetch is None:
            self._write_record()
            return

        reread: Dict[str, List[Bar]] = {}
        for timeframe, bars in self._bars_by_timeframe.items():
            if not self._seam_open(timeframe, bars, first_tick_time):
                continue
            count = self._required_by_timeframe.get(timeframe, len(bars))
            try:
                fresh = self._fetch(timeframe, count, first_tick_time)
            except Exception as error:   # noqa: BLE001 — becomes the refusal, with its cause
                raise WarmupHistoryRefusedError(
                    f'{self._symbol} {timeframe}: a period ended between the warmup read and the '
                    f'first tick ({first_tick_time.isoformat()}), and reading the venue again '
                    f'failed: {error}. Refusing before the workers and the decision run on that '
                    f'tick rather than trading on a history with a missing bar — restart once '
                    f'the venue answers again.'
                ) from error
            if self._seam_open(timeframe, fresh, first_tick_time):
                newest = fresh[-1].timestamp if fresh else 'none'
                raise WarmupHistoryRefusedError(
                    f'{self._symbol} {timeframe}: the venue has not yet committed the period '
                    f'before the first tick ({first_tick_time.isoformat()}); its newest '
                    f'committed bar opens at {newest}. Refusing before the workers and the '
                    f'decision run on that tick rather than trading on a history with a '
                    f'missing bar — the venue commits a period within about two seconds of '
                    f'its end, so a restart a few seconds later closes it.'
                )
            reread[timeframe] = fresh

        if not reread:
            self._write_record()
            return

        bar_controller.inject_warmup_bars(self._symbol, reread, first_tick_time)
        self._bars_by_timeframe.update(reread)
        self._reference_time = first_tick_time
        self._reread_at_first_tick = list(reread)
        self._logger.info(
            f"🔁 Warmup seam closed at the first tick ({first_tick_time.isoformat()}): "
            f"re-read {', '.join(f'{tf}:{len(bars)}' for tf, bars in reread.items())}"
        )
        self._log_tails()
        self._write_record()

    @staticmethod
    def _seam_open(timeframe: str, bars: List[Bar], first_tick_time: datetime) -> bool:
        """
        Whether a history ends before the period of the first tick begins.

        Args:
            timeframe: The bars' timeframe
            bars: The history, oldest first
            first_tick_time: The first tick's timestamp

        Returns:
            True when at least one period between the history and the first tick is missing
        """
        if not bars:
            return True
        newest_end = period_end(open_time_of(bars[-1]), timeframe)
        return newest_end < period_start(first_tick_time, timeframe)

    def build_record(self) -> WarmupHistoryRecord:
        """
        The history as a run records it.

        Returns:
            The record — the bars as they stand now, and the instants that selected them
        """
        return WarmupHistoryRecord(
            symbol=self._symbol,
            source=self._source,
            read_at=self._read_at.isoformat() if self._read_at else '',
            selected_for=self._reference_time.isoformat() if self._reference_time else '',
            first_tick=self._first_tick_time.isoformat() if self._first_tick_time else None,
            reread_at_first_tick=list(self._reread_at_first_tick),
            timeframes={
                timeframe: WarmupTimeframeRecord(
                    required=self._required_by_timeframe.get(timeframe, len(bars)),
                    bars=[(bar.timestamp, bar.open, bar.high, bar.low, bar.close, bar.volume,
                           bar.tick_count) for bar in bars],
                )
                for timeframe, bars in self._bars_by_timeframe.items()
            },
        )

    def _write_record(self) -> None:
        """
        Write the record through the writer inject_into was given, atomically; nowhere when it
        was given none.

        Returns:
            None
        """
        if self._run_writer is None:
            return
        self._run_writer.write_snapshot(RunEntryMemberId.WARMUP_HISTORY,
                                        json.dumps(asdict(self.build_record()), indent=1))

    def _log_tails(self) -> None:
        """
        Log the newest warmup bar per timeframe — the bar the first rendered bar must follow.

        Returns:
            None
        """
        for timeframe, bars in self._bars_by_timeframe.items():
            if not bars:
                continue
            last = bars[-1]
            self._logger.verbose(
                f'📊 Warmup tail {timeframe}: {last.timestamp} | '
                f'O={last.open:.5f} H={last.high:.5f} '
                f'L={last.low:.5f} C={last.close:.5f} | '
                f'Ticks={last.tick_count}'
            )
