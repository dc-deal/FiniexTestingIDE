"""
The gaps of a segment's window in the divergence report (#512) — the stretches in which one side
did not see what the other saw, whose minutes are left out on both sides.

Four sources: more than the gap bound between two consecutive M1 bars of the archive (the coverage
report's own gaps); a run of missing trade ids spanning more than the gap bound, which counts only
once the venue's trade list shows trades inside it — the venue also allocates ids it never
publishes; an outage the session's tick stream recorded; and more
than the gap bound between two consecutive state frames of a session — a frozen process or a
sleeping host records no outage, so only its frames show it.

Pure: the coverage report's gap list, the window's trade ids, the outage records and the frames are
handed in by the collectors. The files of the backtest's code path — the gap timeline, the
deficiency scan — are left untouched, so a gap found here changes nothing the backtest computes.
"""

from bisect import bisect_right
from datetime import datetime, timedelta, timezone
from typing import List, Mapping, Optional, Sequence, Tuple

import numpy as np

from python.framework.constants.divergence_gate_constants import GAP_BOUND_S
from python.framework.types.api.divergence_report_types import (
    DivergenceGap,
    ExcludedSpan,
    GapList,
    GapSource,
)
from python.framework.types.api.state_frame_types import StateFrame
from python.framework.types.autotrader_types.tick_stream_event_types import TickStreamOutage
from python.framework.types.coverage_report_types import Gap

_GAP_BOUND = timedelta(seconds=GAP_BOUND_S)


def build_gap_list(start: datetime, end: datetime, archive_gaps: Sequence[Gap],
                   trade_id_runs: Sequence[DivergenceGap],
                   outages: Mapping[str, Sequence[TickStreamOutage]],
                   frames: Mapping[str, Sequence[StateFrame]],
                   venue_answers: Optional[Mapping[Tuple[int, int], int]] = None,
                   unreadable: Sequence[str] = ()) -> GapList:
    """
    Every gap of a window, and the stretches the confirmed ones leave out.

    Args:
        start: The window's start
        end: The window's end
        archive_gaps: The coverage report's gaps of the symbol
        trade_id_runs: The runs of missing trade ids over the gap bound, unconfirmed
        outages: Each session's tick-stream outages
        frames: Each session's state frames, in time order
        venue_answers: How many trades the venue published inside each run, by the run's two
            archive trade ids — a run with trades counts as a gap, a run without is refuted, a
            run with no answer waits; None where the venue's list was not read
        unreadable: What could not be read, carried into the list

    Returns:
        The gaps that count, the runs still waiting or refuted, and the merged stretches
    """
    runs = [_answered(run, venue_answers or {}) for run in trade_id_runs]
    gaps = [gap for gap in (archive_gaps_in(archive_gaps, start, end)
                            + [run for run in runs if run.confirmed]
                            + [gap for run_id, recorded in sorted(outages.items())
                               for gap in outage_gaps(run_id, recorded)
                               if gap.start < end and gap.end > start]
                            + [gap for run_id, recorded in sorted(frames.items())
                               for gap in frame_pauses(run_id, recorded)
                               if gap.start < end and gap.end > start])]
    gaps.sort(key=lambda gap: (gap.start, gap.end))
    return GapList(gaps=gaps,
                   unconfirmed=[run for run in runs if run.venue_trades is None],
                   refuted=[run for run in runs if run.venue_trades == 0],
                   excluded=merged_spans(gaps), unreadable=list(unreadable))


def _answered(run: DivergenceGap, venue_answers: Mapping[Tuple[int, int], int]) -> DivergenceGap:
    """
    A run of missing trade ids with the venue's answer on it.

    Args:
        run: The run, unconfirmed
        venue_answers: How many trades the venue published inside each run, by its two ids

    Returns:
        The run, confirmed where the venue published trades inside it
    """
    answer = venue_answers.get((run.trade_id_before, run.trade_id_after))
    if answer is None:
        return run
    found = (f'the venue published {answer} trade(s) in it' if answer
             else 'the venue published none of these ids — allocated without a trade')
    return run.model_copy(update={'confirmed': answer > 0, 'venue_trades': answer,
                                  'detail': f'{run.detail} · {found}'})


def archive_gaps_in(archive_gaps: Sequence[Gap], start: datetime,
                    end: datetime) -> List[DivergenceGap]:
    """
    The archive's gaps that touch a window — a gap across one of its edges included, which the
    coverage report's own window query leaves out.

    Args:
        archive_gaps: The coverage report's gaps
        start: The window's start
        end: The window's end

    Returns:
        One gap per archive gap that overlaps the window, by its two bar openings
    """
    found: List[DivergenceGap] = []
    for gap in archive_gaps:
        if gap.gap_start is None or gap.gap_end is None:
            continue
        gap_start, gap_end = _utc(gap.gap_start), _utc(gap.gap_end)
        if gap_start < end and gap_end > start:
            found.append(DivergenceGap(
                source=GapSource.ARCHIVE_BARS, start=gap_start, end=gap_end,
                detail=f'{gap.gap_seconds:.0f} s between two M1 bars ({gap.category.value})'))
    return found


def trade_id_runs(trade_ids: np.ndarray, event_msc: np.ndarray) -> List[DivergenceGap]:
    """
    The runs of trade ids the ticks lack, where a run spans more than the gap bound — between the
    trade before it and the trade after it, by their event times.

    Args:
        trade_ids: The window's trade ids, one per tick
        event_msc: Each tick's event time in epoch milliseconds

    Returns:
        One unconfirmed gap per long run, in id order
    """
    if len(trade_ids) < 2:
        return []
    order = np.argsort(trade_ids, kind='stable')
    ids, times = trade_ids[order], event_msc[order]
    runs: List[DivergenceGap] = []
    for index in np.flatnonzero(np.diff(ids) > 1):
        before, after = _from_msc(times[index]), _from_msc(times[index + 1])
        if after - before <= _GAP_BOUND:
            continue
        first, last = int(ids[index]) + 1, int(ids[index + 1]) - 1
        runs.append(DivergenceGap(
            source=GapSource.TRADE_ID_RUN, start=before, end=after, confirmed=False,
            trade_id_before=int(ids[index]), trade_id_after=int(ids[index + 1]),
            detail=f'trade ids {first}–{last} ({last - first + 1} missing) over '
                   f'{(after - before).total_seconds():.0f} s'))
    return runs


def outage_gaps(run_id: str, outages: Sequence[TickStreamOutage]) -> List[DivergenceGap]:
    """
    A session's tick-stream outages as gaps — from the last trade before the loss to the first
    after it, by the venue's times, where the record carries them; else from the loss to the
    restoration, as the session noticed them.

    Args:
        run_id: The session
        outages: Its recorded outages

    Returns:
        One gap per outage that has an end
    """
    gaps: List[DivergenceGap] = []
    for outage in outages:
        if outage.last_public_trade_time is not None and outage.first_public_trade_time:
            start, end = outage.last_public_trade_time, outage.first_public_trade_time
            measured = "between the venue's trades"
        elif outage.restored_at is not None:
            start, end = outage.lost_at, outage.restored_at
            measured = 'from the loss to the restoration'
        else:
            continue
        gaps.append(DivergenceGap(
            source=GapSource.TICK_STREAM_OUTAGE, start=_utc(start), end=_utc(end), run_id=run_id,
            detail=f'outage {outage.outage_no} ({outage.detected_by.value}), {measured}'))
    return gaps


def frame_pauses(run_id: str, frames: Sequence[StateFrame]) -> List[DivergenceGap]:
    """
    The pauses in a session's state frames longer than the gap bound.

    Args:
        run_id: The session
        frames: Its frames, in time order

    Returns:
        One gap per pause, from the frame before it to the frame after it
    """
    return [DivergenceGap(
        source=GapSource.FRAME_PAUSE, start=earlier.frame_time, end=later.frame_time,
        run_id=run_id,
        detail=f'{(later.frame_time - earlier.frame_time).total_seconds():.0f} s without a frame')
        for earlier, later in zip(frames, frames[1:])
        if later.frame_time - earlier.frame_time > _GAP_BOUND]


def merged_spans(gaps: Sequence[DivergenceGap]) -> List[ExcludedSpan]:
    """
    Confirmed gaps merged into the stretches they leave out.

    Args:
        gaps: The gaps

    Returns:
        Non-overlapping stretches, in time order
    """
    spans: List[ExcludedSpan] = []
    for gap in sorted((gap for gap in gaps if gap.confirmed), key=lambda gap: gap.start):
        if spans and gap.start <= spans[-1].end:
            if gap.end > spans[-1].end:
                spans[-1] = ExcludedSpan(start=spans[-1].start, end=gap.end)
        else:
            spans.append(ExcludedSpan(start=gap.start, end=gap.end))
    return spans


def in_excluded(instant: Optional[datetime], spans: Sequence[ExcludedSpan]) -> bool:
    """
    Whether an instant lies inside an excluded stretch.

    Args:
        instant: The instant; None is never inside
        spans: Non-overlapping stretches, in time order

    Returns:
        True for an instant at or after a stretch's start and before its end
    """
    if instant is None or not spans:
        return False
    index = bisect_right([span.start for span in spans], instant) - 1
    return index >= 0 and instant < spans[index].end


def _from_msc(msc: int) -> datetime:
    """
    An epoch-millisecond time as a UTC instant.

    Args:
        msc: Milliseconds since the epoch

    Returns:
        The instant
    """
    return datetime.fromtimestamp(int(msc) / 1000, tz=timezone.utc)


def _utc(instant: datetime) -> datetime:
    """
    An instant made timezone-aware — the coverage report's gaps may carry naive UTC times.

    Args:
        instant: The instant

    Returns:
        The same instant in UTC
    """
    return instant.replace(tzinfo=timezone.utc) if instant.tzinfo is None else instant
