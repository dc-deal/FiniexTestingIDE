"""
Gap Listing Tests (#599).

A coverage report over years of a venue's history held tens of thousands of gaps and printed
every moderate and large one, while of the short ones it printed the FIRST twenty — the oldest,
the least useful. Both coverage reports now list the newest gaps of each category and count the
rest. Per category, so a burst of recent short gaps never pushes out the one large gap.
"""

from datetime import datetime, timedelta, timezone

from python.framework.discoveries.data_coverage.gap_listing import (
    GAPS_LISTED_PER_CATEGORY,
    gap_span,
    listing_heading,
    newest_gaps,
    recent_gap_listing,
)
from python.framework.types.coverage_report_types import Gap, GapCategory

EPOCH = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _gap(start: datetime, minutes: float, category: GapCategory) -> Gap:
    """
    A gap of a category and a length.

    Args:
        start: Gap start
        minutes: Length in minutes
        category: Its category

    Returns:
        The gap
    """
    return Gap(gap_seconds=minutes * 60, category=category, reason='test',
               gap_start=start, gap_end=start + timedelta(minutes=minutes))


class TestTheNewestOfACategory:

    def test_newest_first_and_no_more_than_the_limit(self):
        gaps = [_gap(EPOCH + timedelta(days=day), 5, GapCategory.SHORT) for day in range(40)]
        listed = newest_gaps(gaps, GapCategory.SHORT)
        assert len(listed) == GAPS_LISTED_PER_CATEGORY
        assert listed == sorted(gaps, key=lambda gap: gap.gap_start,
                                reverse=True)[:GAPS_LISTED_PER_CATEGORY]

    def test_a_burst_of_short_gaps_never_pushes_out_the_one_large_gap(self):
        large = _gap(EPOCH, 600, GapCategory.LARGE)
        burst = [_gap(EPOCH + timedelta(days=30, minutes=10 * i), 5, GapCategory.SHORT)
                 for i in range(500)]
        lines = recent_gap_listing([large] + burst, lambda gap: str(gap.gap_start))
        assert str(large.gap_start) in lines

    def test_categories_are_listed_large_first_and_expected_closures_never(self):
        gaps = [_gap(EPOCH, 5, GapCategory.SHORT), _gap(EPOCH + timedelta(days=1), 600,
                                                       GapCategory.LARGE),
                _gap(EPOCH + timedelta(days=2), 60, GapCategory.MODERATE),
                _gap(EPOCH + timedelta(days=3), 2880, GapCategory.WEEKEND)]
        headings = [line for line in recent_gap_listing(gaps, lambda gap: '   line')
                    if not line.startswith('   ')]
        assert [heading.split(' — ')[0].split()[-1] for heading in headings] == [
            'LARGE', 'MODERATE', 'SHORT']


class TestTheHeadingSaysHowMuchIsShown:

    def test_a_cut_list_names_both_numbers(self):
        assert listing_heading('🔴', GapCategory.LARGE, 10, 1034) == '🔴 LARGE — the last 10 of 1,034'

    def test_a_whole_list_says_so(self):
        assert listing_heading('🔴', GapCategory.LARGE, 3, 3) == '🔴 LARGE — all 3'


class TestTheSpan:

    def test_the_same_day_shows_the_end_time_only(self):
        assert gap_span(_gap(datetime(2026, 7, 29, 15, 34, tzinfo=timezone.utc), 308,
                             GapCategory.LARGE)).split() == ['2026-07-29', '15:34', '→', '20:42']

    def test_another_day_shows_the_end_date(self):
        assert gap_span(_gap(datetime(2026, 9, 20, 19, 48, tzinfo=timezone.utc), 771,
                             GapCategory.LARGE)).split() == ['2026-09-20', '19:48', '→', '09-21',
                                                            '08:39']

    def test_another_year_shows_the_end_year(self):
        assert gap_span(_gap(datetime(2025, 12, 31, 22, 0, tzinfo=timezone.utc), 240,
                             GapCategory.LARGE)).split()[3] == '2026-01-01'
