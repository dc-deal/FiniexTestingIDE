"""
FiniexTestingIDE - Signal Boot Bridge
What a live session knows before its first envelope arrives (#468).

Without this a live session starts BLIND: its SIGNAL workers hold nothing, and the first
decision waits for the producer's next pass — up to a full cadence. On a thirty-day
unattended run that is not a corner case, it is every restart, and a restart at 03:00 is
exactly when nobody is watching.

The bridge mounts the archive slice the session would otherwise ignore and takes its last
(stream_epoch, seq) as the stream's connect cursor. Boot state becomes STALE rather than
BLIND — knowing something old is a strictly better input to a staleness contract than
knowing nothing, because "old" is a fact a decision logic can act on and "nothing" is not.

Read ONCE, at boot. Nothing re-reads the archive during a session: from the first frame
on, the stream is the only thing that extends the series.
"""

from datetime import datetime, timedelta
from typing import Optional

from python.data_management.index.signal_index_manager import SignalIndexManager
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.signal_data.signal_parquet_reader import (
    load_signal_series_from_parquet,
)
from python.framework.types.signal_data_types import (
    SignalBootMount,
    SignalSeries,
    SignalSnapshot,
    SignalStreamCursor,
)


class SignalBootBridge:
    """
    Builds the archive slice and the connect cursor a live session starts from.

    Stateless: it reads what is on disk and returns a verdict. Mounting the series into
    the workers is the caller's job, the same way the resolver decides the mode and the
    callers act on it.
    """

    @staticmethod
    def mount(
        pipeline_id: str,
        symbol: str,
        signal_kind: str,
        replay_window_hours: float,
        now: datetime,
        logger: ScenarioLogger,
        index_manager: Optional[SignalIndexManager] = None,
    ) -> SignalBootMount:
        """
        Read the archive slice for one pipeline and derive the stream's connect cursor.

        The slice is bounded by the producer's own replay window, so the mounted archive
        and the bounded replay meet rather than overlap or leave a hole. The index returns
        the carrier of the last snapshot at or before the window's start as well, which is
        what keeps an archive older than the window from mounting as nothing.

        Args:
            pipeline_id: Producer pipeline the session reads
            symbol: Symbol the series is projected to
            signal_kind: Payload kind the series is filed under
            replay_window_hours: How far back the producer will replay — the same bound
                the slice uses
            now: Session start instant. Passed in rather than read here so the one
                wall-clock observation this makes is visible at its caller
            logger: Session logger
            index_manager: Signal index; built when not supplied

        Returns:
            The mounted slice, its cursor, and whether that cursor is already beyond what
            the producer would replay
        """
        empty = SignalSeries(signal_kind=signal_kind, snapshots=[])
        if not pipeline_id:
            return SignalBootMount(
                series=empty,
                reason='no pipeline configured — the session starts empty')

        start = now - timedelta(hours=replay_window_hours)
        index = index_manager or SignalIndexManager(logger=logger)
        # Loaded before it is asked. Every other caller does this — the shared data
        # preparator builds it right after constructing one — and the first version here
        # did not: it queried an EMPTY index and reported "no archived signals, the session
        # starts blind". That message described this omission and not the archive, which is
        # the worst way for it to be wrong, because blind is a legitimate answer and nobody
        # would have looked twice. `build_index` auto-loads or rebuilds, so calling it is
        # cheap when the index is already on disk.
        index.build_index()
        files = index.get_relevant_files(pipeline_id, symbol, start, now)
        if not files:
            return SignalBootMount(
                series=empty,
                reason=(f"no archived signals for '{pipeline_id}' / {symbol} — the "
                        f'session starts blind until the first envelope arrives'))

        series = load_signal_series_from_parquet(
            files, signal_kind=signal_kind, symbol=symbol, start=start, end=now)
        if not series.snapshots:
            return SignalBootMount(
                series=empty,
                reason=(f"archive holds no {symbol} snapshot for '{pipeline_id}' in the "
                        f'last {replay_window_hours:.0f}h — the session starts blind'))

        cursor = SignalBootBridge._cursor_of(series)
        newest = series.snapshots[-1]
        age_hours = (now - newest.get_resolution_key()).total_seconds() / 3600.0
        beyond = age_hours > replay_window_hours

        if cursor is None:
            reason = (f'{len(series.snapshots)} archived snapshot(s) mounted, newest '
                      f'{age_hours:.1f}h old — no cursor (pre-stream archive), so the '
                      f'stream connects for the current snapshot')
        else:
            reason = (f'{len(series.snapshots)} archived snapshot(s) mounted, newest '
                      f'{age_hours:.1f}h old, cursor {cursor.describe()}')
        return SignalBootMount(
            series=series, cursor=cursor, reason=reason, beyond_replay_window=beyond)

    @staticmethod
    def _cursor_of(series: SignalSeries) -> Optional[SignalStreamCursor]:
        """
        The newest position in the slice that carries a stream identity.

        Searched from the newest backwards rather than taken from the last row, because an
        archive spanning the pre-stream boundary holds both shapes and the identity-bearing
        rows are the recent ones.

        Args:
            series: The mounted slice

        Returns:
            The cursor, or None when no snapshot carries one
        """
        for snapshot in reversed(series.snapshots):
            if SignalBootBridge._has_identity(snapshot):
                return SignalStreamCursor(
                    epoch=snapshot.stream_epoch, seq=snapshot.seq)
        return None

    @staticmethod
    def _has_identity(snapshot: SignalSnapshot) -> bool:
        """
        Whether a snapshot carries both halves of a cursor.

        Args:
            snapshot: The snapshot to check

        Returns:
            True when epoch and seq are both present — one alone is not a position
        """
        return snapshot.seq is not None and snapshot.stream_epoch is not None
