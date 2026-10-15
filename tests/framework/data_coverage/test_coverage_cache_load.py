"""
Coverage Cache Load Tests (#599).

Every run that validates a scenario loads the symbol's coverage report from the discovery
cache. It hydrated the gaps row by row, which cost 3.4 s for a symbol with years of a venue's
history (117,757 gaps, measured 2026-10-15) against 20 ms for one with a year of our own
collection. It now reads them column by column — and must hand back exactly what the row-wise
read did.
"""

from datetime import datetime, timedelta, timezone

import pytest

from python.framework.discoveries.data_coverage.data_coverage_report import DataCoverageReport
from python.framework.discoveries.data_coverage.data_coverage_report_cache import (
    DataCoverageReportCache,
)
from python.framework.types.coverage_report_types import Gap, GapCategory
from python.framework.types.trading_env_types.broker_types import BrokerType

SYMBOL = 'NOSUCHSYM'
BROKER = 'kraken_spot'


@pytest.fixture
def cache(tmp_path):
    """A coverage cache that writes into the test's own directory."""
    cache = DataCoverageReportCache()
    cache.cache_dir = tmp_path
    return cache


def _report(*gaps: Gap) -> DataCoverageReport:
    """
    A report holding the given gaps.

    Args:
        gaps: Its gaps

    Returns:
        The report
    """
    report = DataCoverageReport(symbol=SYMBOL, broker_type=BROKER)
    report.start_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    report.end_time = datetime(2026, 2, 1, tzinfo=timezone.utc)
    report.gaps = list(gaps)
    for gap in gaps:
        report.gap_counts[gap.category.value] += 1
    return report


def _round_trip(cache: DataCoverageReportCache, report: DataCoverageReport) -> DataCoverageReport:
    """Write a report to the cache and read it back."""
    cache._save_to_cache(BROKER, SYMBOL, report)
    return cache._load_from_cache(BROKER, SYMBOL, BrokerType(BROKER))


class TestTheColumnWiseRead:

    def test_hands_back_every_gap_as_it_was_written(self, cache):
        start = datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc)
        written = [
            Gap(gap_seconds=600.0, category=GapCategory.SHORT, reason='⚠️  Short interruption (10 min)',
                gap_start=start, gap_end=start + timedelta(minutes=10)),
            Gap(gap_seconds=18000.0, category=GapCategory.LARGE, reason='🔴 Large gap (5.00h)',
                gap_start=start + timedelta(days=2), gap_end=start + timedelta(days=2, hours=5)),
        ]
        loaded = _round_trip(cache, _report(*written))

        assert [(g.gap_seconds, g.category, g.reason, g.gap_start, g.gap_end)
                for g in loaded.gaps] == [
            (g.gap_seconds, g.category, g.reason, g.gap_start, g.gap_end) for g in written]

    def test_the_times_come_back_as_utc_datetimes(self, cache):
        start = datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc)
        loaded = _round_trip(cache, _report(Gap(
            gap_seconds=600.0, category=GapCategory.SHORT, reason='test',
            gap_start=start, gap_end=start + timedelta(minutes=10))))

        gap = loaded.gaps[0]
        assert type(gap.gap_start) is datetime and gap.gap_start.utcoffset() == timedelta(0)

    def test_a_report_without_gaps_reads_as_one_without_gaps(self, cache):
        # Its columns carry no type, which a column-wise read must not take for a broken file —
        # a None here would regenerate the report on every run
        loaded = _round_trip(cache, _report())

        assert loaded is not None
        assert loaded.gaps == []
