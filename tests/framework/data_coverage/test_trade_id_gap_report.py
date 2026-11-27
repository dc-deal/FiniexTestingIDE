"""
How Many Trades Our Files Miss — Coverage Report Tests (#647).

The tick index measures, per file, the venue's trade ids a file does not hold — where every trade
carries its id. The coverage report sums them per origin, so our own collection's completeness is
read apart from a venue's published history, and names the stretches whose files carry no ids as
not measurable rather than counting them as complete.

The entries are built here; nothing reads this machine's archive.
"""

from datetime import datetime, timezone
from unittest.mock import patch

from python.framework.discoveries.data_coverage.data_coverage_report import DataCoverageReport
from python.framework.discoveries.data_coverage.trade_id_gap_summary import build_trade_id_gap_report
from python.framework.types.data_deficiency_types import TradeIdGaps


def _entry(origin_class: str, version: str, day: int, gaps=None) -> dict:
    """
    A tick index entry with what the summary reads.

    Args:
        origin_class: Whose file it is
        version: Its data format version
        day: The November day it covers
        gaps: Its trade-id measurement, None for a file without trade ids

    Returns:
        The entry
    """
    return {
        'origin_class': origin_class, 'data_format_version': version, 'tick_count': 100,
        'start_time': datetime(2026, 11, day, 0, 0, tzinfo=timezone.utc),
        'end_time': datetime(2026, 11, day, 23, 0, tzinfo=timezone.utc),
        'trade_id_gaps': gaps,
    }


def _gaps(span: int, missing: int, longest_run: int, seconds: float) -> TradeIdGaps:
    return TradeIdGaps(span=span, missing=missing, runs=1 if missing else 0,
                       longest_run=longest_run, longest_run_seconds=seconds)


class TestMissingTradesPerOrigin:

    def test_the_measured_files_are_summed_per_origin_in_declared_order(self):
        report = build_trade_id_gap_report([
            _entry('external', '1.7.0', 1, _gaps(1000, 0, 0, 0.0)),
            _entry('production', '1.7.0', 2, _gaps(1000, 10, 7, 12.0)),
            _entry('production', '1.7.0', 3, _gaps(500, 20, 20, 400.0)),
        ])

        assert [summary.origin_class for summary in report.summaries] == ['production', 'external']
        production = report.summaries[0]
        assert (production.measured_files, production.span, production.missing) == (2, 1500, 30)
        assert (production.longest_run, production.longest_run_seconds) == (20, 400.0)
        # The second file's run lasted longer than decision 7's five minutes
        assert production.files_over_limit == 1
        assert report.summaries[1].missing == 0

    def test_a_stretch_without_trade_ids_is_named_not_counted(self):
        report = build_trade_id_gap_report([
            _entry('production', '1.5.0', 1),
            _entry('production', '1.5.0', 2),
            _entry('production', '1.7.0', 3, _gaps(1000, 0, 0, 0.0)),
        ])

        assert [(span.version, span.file_count) for span in report.unmeasured_spans] == [('1.5.0', 2)]
        assert report.summaries[0].measured_files == 1

    def test_no_file_with_trade_ids_yields_no_summary_never_a_zero(self):
        report = build_trade_id_gap_report([_entry('production', '1.3.0', 1)])

        assert report.summaries == []
        assert len(report.unmeasured_spans) == 1


class TestTheSectionReads:
    """The report's section, as printed: per origin, and the unmeasured stretch named."""

    def test_the_section_names_each_origin_and_what_is_not_measurable(self):
        report = DataCoverageReport(symbol='ETHUSD', broker_type='kraken_spot')
        report.start_time = datetime(2026, 11, 1, tzinfo=timezone.utc)
        report.end_time = datetime(2026, 11, 4, tzinfo=timezone.utc)
        entries = [
            _entry('production', '1.5.0', 1),
            _entry('production', '1.7.0', 2, _gaps(1000, 10, 7, 12.0)),
        ]
        with patch.object(DataCoverageReport, 'attribute_gaps'), \
                patch.object(DataCoverageReport, '_version_spans_section', return_value=[]), \
                patch.object(DataCoverageReport, '_tick_entries', return_value=entries):
            text = report.generate_report()

        assert 'MISSING TRADES (venue trade ids):' in text
        assert 'production   1 file(s) · ids 1,000 · missing 10 (1.000 %)' in text
        assert '(format 1.5.0, 1 file(s)): not measurable' in text
