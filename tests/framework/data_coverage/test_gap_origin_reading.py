"""
Whose Silence a Gap Is — Coverage Report Tests (#599).

Since a venue's published history entered the archive, most gaps of a Kraken symbol lie in data
no collector of ours recorded: hours in which nobody traded at the venue. The report read every
one of them as a collector fault — "the collector was running", "check data collection", advice
written for MT5's collector printed for every broker. It now reads each gap by the origin class
of the tick files around it, counts them per year and origin, and recommends only for our own
collection, with the MT5 advice for an MT5 broker only.

The gaps here carry their files and origin classes already; the tick-index read that stamps
them is replaced, so the tests do not depend on this machine's archive.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional
from unittest.mock import patch

import pytest

from python.framework.discoveries.data_coverage.data_coverage_report import DataCoverageReport
from python.framework.types.config_types.data_origin_config_types import OriginClass
from python.framework.types.coverage_report_types import Gap, GapCategory

EXTERNAL = OriginClass.EXTERNAL
PRODUCTION = OriginClass.PRODUCTION


def _gap(start: datetime, hours: float, category: GapCategory,
         before: Optional[OriginClass], after: Optional[OriginClass] = None,
         file_before: Optional[str] = 'A.parquet', file_after: Optional[str] = 'A.parquet',
         resumed_s: Optional[float] = None) -> Gap:
    """
    A gap with its files and origin classes stamped, as the attribution leaves it.

    Args:
        start: Gap start
        hours: Length in hours
        category: Its category
        before: Origin class of the file it lies in or follows (None: unattributed)
        after: Origin class of the file after it (defaults to `before`)
        file_before: File before the gap (None: unattributed)
        file_after: File after the gap
        resumed_s: Seconds after the preceding data until the next file opened

    Returns:
        The gap
    """
    return Gap(gap_seconds=hours * 3600, category=category, reason='test',
               gap_start=start, gap_end=start + timedelta(hours=hours),
               file_before=file_before, file_after=file_after,
               next_file_opened_after_s=resumed_s,
               origin_class_before=before,
               origin_class_after=after if after is not None else before)


def _report(broker_type: str, *gaps: Gap) -> DataCoverageReport:
    """
    A report holding the given gaps, its tick-index reads replaced.

    Args:
        broker_type: The broker the report is for
        gaps: Its gaps

    Returns:
        The report; use it inside the `_attributed` patch
    """
    report = DataCoverageReport(symbol='BTCUSD', broker_type=broker_type)
    report.start_time = datetime(2013, 1, 1, tzinfo=timezone.utc)
    report.end_time = datetime(2026, 10, 1, tzinfo=timezone.utc)
    report.gaps = list(gaps)
    for gap in gaps:
        report.gap_counts[gap.category.value] += 1
    return report


@pytest.fixture
def unread_index():
    """The tick index is never read: the gaps are attributed already."""
    with patch.object(DataCoverageReport, 'attribute_gaps'), \
            patch.object(DataCoverageReport, '_version_spans_section', return_value=[]):
        yield


T = datetime(2025, 11, 1, 15, 2, tzinfo=timezone.utc)


@pytest.mark.usefixtures('unread_index')
class TestWhoseSilenceAGapIs:

    def test_a_gap_in_the_venue_s_history_is_no_trades_at_the_venue(self):
        gap = _gap(T, 6.7, GapCategory.LARGE, EXTERNAL)
        location = _report('kraken_spot')._gap_location(gap)
        assert location == 'inside A.parquet — no trades at the venue, nothing to re-collect'

    def test_between_two_files_of_the_venue_s_history_too(self):
        gap = _gap(T, 6.7, GapCategory.LARGE, EXTERNAL, file_after='B.parquet', resumed_s=24000)
        location = _report('kraken_spot')._gap_location(gap)
        assert 'no trades at the venue' in location and 'collection' not in location

    def test_the_seam_between_two_origins_is_named_as_such(self):
        gap = _gap(T, 0.2, GapCategory.SHORT, EXTERNAL, PRODUCTION, file_after='B.parquet')
        assert _report('kraken_spot')._gap_location(gap) == (
            'where the external data ends and the production data begins')

    def test_inside_our_own_file_the_collector_was_running(self):
        gap = _gap(T, 5.1, GapCategory.LARGE, PRODUCTION)
        assert _report('kraken_spot')._gap_location(gap) == (
            'inside A.parquet, the collector was running')

    @pytest.mark.parametrize('resumed_s, expected', [
        (12, 'between two files, at a rollover — collection continued'),
        (46217, 'between two files, collection resumed 12h 50m later'),
        (None, 'between two files'),
    ])
    def test_between_two_of_our_files_it_says_when_collection_resumed(self, resumed_s, expected):
        gap = _gap(T, 12.8, GapCategory.LARGE, PRODUCTION, file_after='B.parquet',
                   resumed_s=resumed_s)
        assert _report('kraken_spot')._gap_location(gap) == expected

    def test_a_gap_no_tick_file_was_found_for_says_so(self):
        gap = _gap(T, 23.0, GapCategory.LARGE, None, file_before=None, file_after=None)
        assert _report('kraken_spot')._gap_location(gap) == 'unattributed (no tick index entry)'


@pytest.mark.usefixtures('unread_index')
class TestTheYearlySummaryByOrigin:

    def _rows(self):
        report = _report(
            'kraken_spot',
            _gap(datetime(2016, 3, 1, tzinfo=timezone.utc), 0.1, GapCategory.SHORT, EXTERNAL),
            _gap(datetime(2016, 3, 2, tzinfo=timezone.utc), 1.0, GapCategory.MODERATE, EXTERNAL),
            _gap(datetime(2016, 3, 3, tzinfo=timezone.utc), 48.0, GapCategory.WEEKEND, EXTERNAL),
            _gap(datetime(2025, 12, 31, 22, tzinfo=timezone.utc), 5.0, GapCategory.LARGE,
                 EXTERNAL, PRODUCTION, file_after='B.parquet'),
            _gap(datetime(2026, 2, 1, tzinfo=timezone.utc), 0.5, GapCategory.SHORT, PRODUCTION),
            _gap(datetime(2026, 3, 1, tzinfo=timezone.utc), 2.0, GapCategory.MODERATE, None,
                 file_before=None, file_after=None),
        )
        return {(row.year, row.origin_class): row for row in report.summary_by_year_and_origin()}

    def test_counts_per_year_and_origin(self):
        rows = self._rows()
        assert (rows[(2016, EXTERNAL)].short, rows[(2016, EXTERNAL)].moderate) == (1, 1)
        assert rows[(2026, PRODUCTION)].short == 1

    def test_expected_closures_are_not_missing_data(self):
        assert self._rows()[(2016, EXTERNAL)].hours_without_data == pytest.approx(1.1)

    def test_a_gap_belongs_to_the_year_it_started_and_the_file_it_follows(self):
        # The seam gap starts on New Year's Eve in the venue's history and ends in ours
        assert self._rows()[(2025, EXTERNAL)].large == 1

    def test_an_unattributed_gap_has_a_row_of_its_own(self):
        assert self._rows()[(2026, None)].moderate == 1

    def test_rows_run_by_year_and_the_unattributed_come_last(self):
        assert list(self._rows()) == [(2016, EXTERNAL), (2025, EXTERNAL), (2026, PRODUCTION),
                                      (2026, None)]


@pytest.mark.usefixtures('unread_index')
class TestRecommendationsAreForOurOwnCollection:

    def test_the_venue_s_history_alone_needs_none(self):
        report = _report('kraken_spot', _gap(T, 6.7, GapCategory.LARGE, EXTERNAL))
        assert report.get_recommendations() == []
        assert '1 lie in the venue\'s own history (external)' in report.generate_report()

    def test_our_kraken_collection_gets_the_collector_advice_not_mql5(self):
        report = _report('kraken_spot', _gap(T, 5.1, GapCategory.LARGE, PRODUCTION))
        advice = ' '.join(report.get_recommendations())
        assert 'consider re-collecting' in advice and 'MQL5' not in advice

    def test_an_mt5_collection_gets_the_mql5_advice(self):
        report = _report('mt5', _gap(T, 5.1, GapCategory.LARGE, PRODUCTION))
        assert 'Check if MQL5 TickCollector was stopped intentionally' in (
            report.get_recommendations())

    def test_an_unattributed_gap_is_not_ours_to_judge(self):
        report = _report('kraken_spot', _gap(T, 5.1, GapCategory.LARGE, None,
                                             file_before=None, file_after=None))
        assert report.get_recommendations() == []
        assert 'could not be matched to a tick file' in report.generate_report()


@pytest.mark.usefixtures('unread_index')
class TestThePrintedReport:

    def _text(self):
        venue_history = [_gap(datetime(2016, 1, 1, tzinfo=timezone.utc) + timedelta(days=day),
                              0.1, GapCategory.SHORT, EXTERNAL) for day in range(30)]
        ours = [_gap(datetime(2026, 9, 20, 19, 48, tzinfo=timezone.utc), 12.8, GapCategory.LARGE,
                     PRODUCTION, file_after='B.parquet', resumed_s=46217)]
        return _report('kraken_spot', *(venue_history + ours)).generate_report()

    def test_the_newest_ten_of_a_long_category_with_its_count(self):
        assert 'SHORT — the last 10 of 30' in self._text()

    def test_one_line_per_gap_with_its_origin(self):
        line = next(line for line in self._text().split('\n') if line.startswith('   2026-09-20'))
        assert line.endswith('[production]')

    def test_the_summary_by_year_and_origin_is_printed(self):
        text = self._text()
        assert 'BY YEAR AND ORIGIN' in text
        assert any(line.split()[:2] == ['2016', 'external'] for line in text.split('\n'))

    def test_no_gap_in_the_venue_s_history_reads_as_a_collector_fault(self):
        external_lines = [line for line in self._text().split('\n') if '[external]' in line]
        assert external_lines and not any('collector' in line for line in external_lines)
