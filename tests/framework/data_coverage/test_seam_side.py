"""
Which Side of the Seam a Gap Lies On — Gap Type Tests (#647).

The scenario validation, the region extractor and the coverage report's issue count admit a gap
in a venue's published history whatever its category, and judge one in collected data as
before. The side is read from the bar index row alone — its origin classes and the instant our
collection took over — because the batch validation must not open the tick index. Where the row
cannot place a gap, it says so rather than guess the side that admits more.
"""

from datetime import datetime, timedelta, timezone
from typing import List, Optional

import pytest

from python.framework.types.config_types.data_origin_config_types import OriginClass
from python.framework.types.coverage_report_types import Gap, GapCategory, SeamSide
from python.framework.types.data_origin_types import SymbolDataOrigin

EXTERNAL = OriginClass.EXTERNAL
PRODUCTION = OriginClass.PRODUCTION
SEAM = datetime(2026, 1, 24, 14, 20, 20, tzinfo=timezone.utc)


def _gap(end: Optional[datetime], hours: float = 1.0) -> Gap:
    start = (end or SEAM) - timedelta(hours=hours)
    return Gap(gap_seconds=hours * 3600, category=GapCategory.MODERATE, reason='test',
               gap_start=start, gap_end=end)


def _origin(classes: List[OriginClass], seam: Optional[datetime]) -> SymbolDataOrigin:
    return SymbolDataOrigin(origin_classes=classes, collected_since=seam)


class TestTheThreeSides:

    @pytest.mark.parametrize('end, expected', [
        (SEAM - timedelta(days=400), SeamSide.EXTERNAL_HISTORY),
        (SEAM, SeamSide.EXTERNAL_HISTORY),
        (SEAM + timedelta(minutes=1), SeamSide.COLLECTED),
        (SEAM + timedelta(days=30), SeamSide.COLLECTED),
    ])
    def test_a_gap_ending_at_or_before_the_seam_lies_in_the_venue_s_history(self, end, expected):
        assert _gap(end).seam_side(_origin([PRODUCTION, EXTERNAL], SEAM)) == expected

    def test_a_gap_across_the_seam_is_judged_as_collected(self):
        """It ends in our collection: the side that admits more is never guessed."""
        across = _gap(SEAM + timedelta(minutes=10), hours=2.0)
        assert across.seam_side(_origin([PRODUCTION, EXTERNAL], SEAM)) == SeamSide.COLLECTED

    def test_bars_from_the_venue_s_history_alone_have_no_seam(self):
        assert _gap(SEAM).seam_side(_origin([EXTERNAL], None)) == SeamSide.EXTERNAL_HISTORY

    def test_bars_without_external_history_are_collected(self):
        assert _gap(SEAM).seam_side(_origin([PRODUCTION], None)) == SeamSide.COLLECTED

    @pytest.mark.parametrize('origin', [
        None,
        _origin([], None),
        # Both kinds in the file and no seam recorded: the gap cannot be placed
        _origin([PRODUCTION, EXTERNAL], None),
    ])
    def test_a_row_that_cannot_place_the_gap_states_nothing(self, origin):
        assert _gap(SEAM).seam_side(origin) == SeamSide.UNSTATED

    def test_a_gap_without_an_end_states_nothing(self):
        assert _gap(None).seam_side(_origin([EXTERNAL], None)) == SeamSide.UNSTATED
