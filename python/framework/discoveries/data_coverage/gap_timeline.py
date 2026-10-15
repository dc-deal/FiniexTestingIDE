"""
Gap Timeline - Time-Window Queries Over a Coverage Report's Gaps

A report over years of a venue's history holds hundreds of thousands of gaps, and a run's
validation asks about them once per scenario. Measured 2026-10-15: 117,757 gaps for
kraken_spot BTCUSD and 375,682 for LTCUSD, against about 400 for an MT5 symbol with one year of
data — and the two linear scans the validator made cost 166 ms per BTCUSD scenario against
0.7 ms per MT5 one. A binary search over the start times answers in microseconds, whatever
the archive's depth.

Gaps never overlap: each one lies between two consecutive bars, and a gap split at a market
boundary is split into ADJACENT segments. So at most one gap contains a given moment, and the
gaps sorted by start are sorted by end as well.
"""

from bisect import bisect_left
from datetime import datetime
from typing import List, Optional

from python.framework.types.coverage_report_types import Gap
from python.framework.utils.time_utils import ensure_utc_aware


class GapTimeline:
    """
    A report's gaps in start order, built once and queried by time window.

    Holds references to the report's own Gap objects, never copies, so a gap stamped later
    (its file and origin, on the render path) reads the same through either.
    """

    def __init__(self, gaps: List[Gap]):
        """
        Sort the gaps by start time.

        Args:
            gaps: The report's gaps. A gap without a start or an end cannot be placed in time
                and is left out — a detected gap always carries both
        """
        placed = [gap for gap in gaps if gap.gap_start is not None and gap.gap_end is not None]
        # Built once per symbol and batch, so it is kept lean: an aware time compares by its
        # instant whatever its zone, so only a naive one is converted, and the cache hands the
        # gaps over sorted, which a check finds faster than a sort would. Converting and sorting
        # every gap cost 194 ms for 117,757 gaps and 776 ms for 375,682 (measured 2026-10-15).
        starts = [gap.gap_start if gap.gap_start.tzinfo is not None
                  else ensure_utc_aware(gap.gap_start) for gap in placed]
        ends = [gap.gap_end if gap.gap_end.tzinfo is not None
                else ensure_utc_aware(gap.gap_end) for gap in placed]
        if any(later < earlier for earlier, later in zip(starts, starts[1:])):
            order = sorted(range(len(placed)), key=starts.__getitem__)
            placed = [placed[i] for i in order]
            starts = [starts[i] for i in order]
            ends = [ends[i] for i in order]
        self._starts = starts
        self._ends = ends
        self._gaps = placed
        # The list it was built over, and its length then: a report fills its list after
        # construction (the cache hydrates it, analyze() appends), so a timeline built early
        # must be rebuilt rather than answer for gaps it never saw
        self._source = gaps
        self._source_size = len(gaps)

    def covers(self, gaps: List[Gap]) -> bool:
        """
        Whether this timeline answers for exactly this gap list as it stands.

        Args:
            gaps: The report's current gap list

        Returns:
            True when it was built over this very list at its current length
        """
        return gaps is self._source and len(gaps) == self._source_size

    def gaps_in_window(self, start: datetime, end: datetime) -> List[Gap]:
        """
        Gaps fully contained in a window: their start and end both fall inside it.

        Args:
            start: Window start (UTC-aware)
            end: Window end (UTC-aware)

        Returns:
            The contained gaps, oldest first
        """
        contained = []
        for idx in range(bisect_left(self._starts, start), len(self._gaps)):
            if self._starts[idx] > end:
                break
            if self._ends[idx] <= end:
                contained.append(self._gaps[idx])
        return contained

    def gap_containing(self, moment: datetime) -> Optional[Gap]:
        """
        The gap a moment lies strictly inside, if any.

        A moment exactly on a gap's start or end is NOT inside it — that is where data
        exists on one side.

        Args:
            moment: The instant to locate (UTC-aware)

        Returns:
            The gap with gap_start < moment < gap_end, or None
        """
        idx = bisect_left(self._starts, moment) - 1
        if idx < 0:
            return None
        return self._gaps[idx] if moment < self._ends[idx] else None
