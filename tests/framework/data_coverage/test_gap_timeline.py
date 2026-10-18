"""
Gap Timeline Tests (#599).

A run's validation asks a symbol's coverage report two questions per scenario: does a gap
contain the scenario's start, and which gaps lie inside its tick stretch. It asked them as
linear scans over every gap, which cost 166 ms per scenario on a symbol with years of a venue's
history (117,757 gaps) against 0.7 ms on one with a year of our own collection. The timeline
answers by binary search — and must answer EXACTLY what the scans answered, which is what these
tests hold it to: the scans are reproduced below as they stood, and both are asked the same
questions, at the edges above all.
"""

import random
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import pytest

from python.framework.discoveries.data_coverage.gap_timeline import GapTimeline
from python.framework.types.coverage_report_types import Gap, GapCategory

EPOCH = datetime(2020, 1, 1, tzinfo=timezone.utc)


def _gap(start: datetime, end: datetime) -> Gap:
    """
    A gap between two instants.

    Args:
        start: Gap start
        end: Gap end

    Returns:
        The gap
    """
    return Gap(gap_seconds=(end - start).total_seconds(), category=GapCategory.SHORT,
               reason='test', gap_start=start, gap_end=end)


def _scan_containing(gaps: List[Gap], moment: datetime) -> Optional[Gap]:
    """The validator's former start-date scan, as it stood."""
    for gap in gaps:
        if gap.gap_start < moment < gap.gap_end:
            return gap
    return None


def _scan_in_window(gaps: List[Gap], start: datetime, end: datetime) -> List[Gap]:
    """The former window scan, as it stood (tick stretch and signal report alike)."""
    return [gap for gap in gaps if gap.gap_start >= start and gap.gap_end <= end]


def _archive(seed: int, count: int) -> List[Gap]:
    """
    Non-overlapping gaps the way detection produces them: between consecutive bars, some of
    them split at a market boundary into ADJACENT segments.

    Args:
        seed: Random seed
        count: How many gaps

    Returns:
        The gaps in chronological order
    """
    rng = random.Random(seed)
    gaps = []
    cursor = EPOCH
    for _ in range(count):
        if gaps and rng.random() < 0.15:
            start = gaps[-1].gap_end                    # adjacent: a split segment
        else:
            start = cursor + timedelta(minutes=rng.randint(10, 600))
        end = start + timedelta(minutes=rng.randint(1, 300))
        gaps.append(_gap(start, end))
        cursor = end
    return gaps


def _probe_moments(gaps: List[Gap], seed: int) -> List[datetime]:
    """Every edge, a second either side of it, and random instants across the archive."""
    rng = random.Random(seed)
    moments = []
    for gap in gaps:
        for edge in (gap.gap_start, gap.gap_end):
            moments.extend((edge, edge - timedelta(seconds=1), edge + timedelta(seconds=1)))
    span = (gaps[-1].gap_end - EPOCH).total_seconds()
    moments.extend(EPOCH + timedelta(seconds=rng.uniform(-3600, span + 3600))
                   for _ in range(300))
    return moments


@pytest.mark.parametrize('seed', [1, 2, 3, 4, 5])
class TestTheSearchAnswersWhatTheScanAnswered:

    def test_the_gap_containing_a_moment(self, seed):
        gaps = _archive(seed, 400)
        timeline = GapTimeline(gaps)
        for moment in _probe_moments(gaps, seed):
            assert timeline.gap_containing(moment) is _scan_containing(gaps, moment), moment

    def test_the_gaps_inside_a_window(self, seed):
        gaps = _archive(seed, 400)
        timeline = GapTimeline(gaps)
        moments = sorted(_probe_moments(gaps, seed))
        rng = random.Random(seed)
        for _ in range(400):
            start, end = sorted(rng.sample(moments, 2))
            assert timeline.gaps_in_window(start, end) == _scan_in_window(gaps, start, end)

    def test_a_window_exactly_on_a_gap_s_edges_holds_it(self, seed):
        gaps = _archive(seed, 50)
        timeline = GapTimeline(gaps)
        for gap in gaps:
            assert gap in timeline.gaps_in_window(gap.gap_start, gap.gap_end)


class TestTheEdges:

    def test_a_moment_on_an_edge_is_not_inside(self):
        gap = _gap(EPOCH, EPOCH + timedelta(hours=1))
        timeline = GapTimeline([gap])
        assert timeline.gap_containing(EPOCH) is None
        assert timeline.gap_containing(EPOCH + timedelta(hours=1)) is None
        assert timeline.gap_containing(EPOCH + timedelta(minutes=30)) is gap

    def test_the_seam_between_two_split_segments_is_in_neither(self):
        first = _gap(EPOCH, EPOCH + timedelta(hours=1))
        second = _gap(EPOCH + timedelta(hours=1), EPOCH + timedelta(hours=2))
        assert GapTimeline([first, second]).gap_containing(EPOCH + timedelta(hours=1)) is None

    def test_an_unsorted_list_is_sorted_first(self):
        gaps = _archive(9, 30)
        shuffled = list(gaps)
        random.Random(9).shuffle(shuffled)
        timeline = GapTimeline(shuffled)
        assert timeline.gaps_in_window(EPOCH, gaps[-1].gap_end) == gaps

    def test_a_gap_without_a_time_is_left_out(self):
        placed = _gap(EPOCH, EPOCH + timedelta(hours=1))
        unplaced = Gap(gap_seconds=60.0, category=GapCategory.SHORT, reason='test')
        timeline = GapTimeline([unplaced, placed])
        assert timeline.gaps_in_window(EPOCH, EPOCH + timedelta(days=1)) == [placed]

    def test_a_naive_time_reads_as_utc(self):
        naive = Gap(gap_seconds=3600.0, category=GapCategory.SHORT, reason='test',
                    gap_start=datetime(2020, 1, 1, 10), gap_end=datetime(2020, 1, 1, 11))
        timeline = GapTimeline([naive])
        assert timeline.gap_containing(datetime(2020, 1, 1, 10, 30, tzinfo=timezone.utc)) is naive

    def test_no_gaps_answers_nothing(self):
        timeline = GapTimeline([])
        assert timeline.gap_containing(EPOCH) is None
        assert timeline.gaps_in_window(EPOCH, EPOCH + timedelta(days=1)) == []


class TestItIsRebuiltWhenTheListChanges:
    """A report fills its list after construction: the cache hydrates it, analyze() appends."""

    def test_it_covers_the_list_it_was_built_over(self):
        gaps = _archive(3, 5)
        assert GapTimeline(gaps).covers(gaps)

    def test_a_grown_list_is_not_covered(self):
        gaps = _archive(3, 5)
        timeline = GapTimeline(gaps)
        gaps.append(_gap(gaps[-1].gap_end + timedelta(hours=1),
                         gaps[-1].gap_end + timedelta(hours=2)))
        assert not timeline.covers(gaps)

    def test_a_replaced_list_is_not_covered(self):
        gaps = _archive(3, 5)
        timeline = GapTimeline(gaps)
        assert not timeline.covers(list(gaps))
