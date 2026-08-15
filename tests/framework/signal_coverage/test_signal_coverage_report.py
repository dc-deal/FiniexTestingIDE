"""
FiniexTestingIDE - SignalCoverageReport Unit Tests

Covers:
- gap detection from the snapshot timeline (measured cadence, 2x tolerance)
- the signal-specific weekend rule: a weekend hole is a REAL gap, never an
  expected closure (the producing engine runs 24/7)
- gap classification against the signal thresholds (large > 1h)
- window queries the scenario validator builds on (blind head, aged head,
  gaps in window, coverage ratio)
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List

import pandas as pd
import pytest

from python.framework.discoveries.signal_coverage.signal_coverage_report import (
    SignalCoverageReport)
from python.framework.types.coverage_report_types import GapCategory
from python.framework.types.signal_data_types import (
    SIGNAL_ENVELOPE_SYMBOL, SignalParquetColumn)

CADENCE = timedelta(minutes=10)


def _write_series(tmp_path: Path, moments: List[datetime], name: str = 'series',
                  origin: str = None) -> Path:
    """
    Write a minimal signal parquet carrying the snapshot timeline.

    Args:
        tmp_path: pytest tmp dir
        moments: Snapshot times (UTC-aware)
        name: File stem
        origin: data_origin value; None omits the column entirely (a pre-contract archive)

    Returns:
        Path of the written parquet
    """
    rows = []
    for moment in moments:
        msc = int(moment.timestamp() * 1000)
        # One envelope sentinel + one symbol row per snapshot — the real shape.
        for symbol in (SIGNAL_ENVELOPE_SYMBOL, 'BTCUSD'):
            row = {SignalParquetColumn.COLLECTED_MSC.value: msc,
                   SignalParquetColumn.SYMBOL.value: symbol}
            if origin is not None:
                row[SignalParquetColumn.DATA_ORIGIN.value] = origin
            rows.append(row)

    path = tmp_path / f'{name}.parquet'
    pd.DataFrame(rows).to_parquet(path)
    return path


def _grid(start: datetime, count: int, step: timedelta = CADENCE) -> List[datetime]:
    """Regular snapshot grid."""
    return [start + step * i for i in range(count)]


@pytest.fixture
def continuous_report(tmp_path):
    """A gapless 10-minute series over 6 hours."""
    start = datetime(2026, 7, 22, 0, 0, tzinfo=timezone.utc)
    path = _write_series(tmp_path, _grid(start, 36))
    report = SignalCoverageReport('crypto_sentiment', 'BTCUSD')
    report.analyze([path])
    return report


class TestCadenceMeasurement:
    """The cadence is measured from the series, not configured."""

    def test_measures_ten_minute_cadence(self, continuous_report):
        assert continuous_report.cadence_seconds == pytest.approx(600.0)

    def test_counts_snapshots_once_per_envelope(self, continuous_report):
        # Two parquet rows per snapshot (sentinel + symbol) collapse to one
        assert continuous_report.snapshot_count == 36

    def test_range_spans_first_to_last(self, continuous_report):
        assert continuous_report.start_time == datetime(
            2026, 7, 22, 0, 0, tzinfo=timezone.utc)
        assert continuous_report.end_time == datetime(
            2026, 7, 22, 5, 50, tzinfo=timezone.utc)

    def test_no_gaps_on_regular_grid(self, continuous_report):
        assert continuous_report.gaps == []
        assert not continuous_report.has_issues()

    def test_jitter_below_tolerance_is_no_gap(self, tmp_path):
        # 97% of real envelopes land within 60s of the bar close — jitter must
        # never register as a gap (tolerance is 2x cadence).
        start = datetime(2026, 7, 22, 0, 0, tzinfo=timezone.utc)
        moments = [m + timedelta(seconds=45 * (i % 2))
                   for i, m in enumerate(_grid(start, 24))]
        path = _write_series(tmp_path, moments)

        report = SignalCoverageReport('crypto_sentiment', 'BTCUSD')
        report.analyze([path])

        assert report.gaps == []


class TestGapClassification:
    """Signal thresholds: short < 30min, moderate < 1h, large above."""

    def _report_with_hole(self, tmp_path, hole: timedelta) -> SignalCoverageReport:
        start = datetime(2026, 7, 22, 0, 0, tzinfo=timezone.utc)
        before = _grid(start, 12)
        after = _grid(before[-1] + hole, 12)
        path = _write_series(tmp_path, before + after)

        report = SignalCoverageReport('crypto_sentiment', 'BTCUSD')
        report.analyze([path])
        return report

    def test_short_gap(self, tmp_path):
        report = self._report_with_hole(tmp_path, timedelta(minutes=22))
        assert len(report.gaps) == 1
        assert report.gaps[0].category == GapCategory.SHORT
        assert not report.has_issues()

    def test_moderate_gap(self, tmp_path):
        report = self._report_with_hole(tmp_path, timedelta(minutes=45))
        assert report.gaps[0].category == GapCategory.MODERATE
        assert report.has_issues()

    def test_large_gap_above_one_hour(self, tmp_path):
        # Operator rule: no server restart takes longer than an hour
        report = self._report_with_hole(tmp_path, timedelta(minutes=75))
        assert report.gaps[0].category == GapCategory.LARGE

    def test_gap_reason_reports_missed_snapshots(self, tmp_path):
        report = self._report_with_hole(tmp_path, timedelta(hours=2))
        assert 'snapshots missed' in report.gaps[0].reason
        assert '~11 snapshots missed' in report.gaps[0].reason


class TestWeekendIsNotExpected:
    """
    The producing engine runs 24/7 — a weekend hole is a real outage.

    Regression guard: reusing the tick report's weekend rule would classify
    this as WEEKEND (expected) and silently absolve the outage.
    """

    def test_friday_to_monday_hole_is_large_not_weekend(self, tmp_path):
        # 2026-07-24 is a Friday, 2026-07-27 a Monday
        friday = datetime(2026, 7, 24, 20, 0, tzinfo=timezone.utc)
        monday = datetime(2026, 7, 27, 8, 0, tzinfo=timezone.utc)
        path = _write_series(tmp_path, _grid(friday, 6) + _grid(monday, 6))

        report = SignalCoverageReport('forex_macro_sentiment', 'EURUSD')
        report.analyze([path])

        assert len(report.gaps) == 1
        assert report.gaps[0].category == GapCategory.LARGE
        assert report.gap_counts['weekend'] == 0


class TestWindowQueries:
    """The surface the scenario validator builds on."""

    def test_snapshot_at_or_before_inside_series(self, continuous_report):
        moment = datetime(2026, 7, 22, 3, 0, tzinfo=timezone.utc)
        assert continuous_report.has_snapshot_at_or_before(moment)

    def test_no_snapshot_before_series_start(self, continuous_report):
        moment = datetime(2026, 7, 21, 23, 0, tzinfo=timezone.utc)
        assert not continuous_report.has_snapshot_at_or_before(moment)

    def test_latest_snapshot_resolves_like_the_worker(self, continuous_report):
        # Worker semantics: nearest snapshot at or before the tick
        moment = datetime(2026, 7, 22, 3, 5, tzinfo=timezone.utc)
        assert continuous_report.latest_snapshot_at_or_before(moment) == \
            datetime(2026, 7, 22, 3, 0, tzinfo=timezone.utc)

    def test_latest_snapshot_none_before_series(self, continuous_report):
        moment = datetime(2026, 7, 21, 23, 0, tzinfo=timezone.utc)
        assert continuous_report.latest_snapshot_at_or_before(moment) is None

    def test_gaps_in_window_only_contained_ones(self, tmp_path):
        start = datetime(2026, 7, 22, 0, 0, tzinfo=timezone.utc)
        before = _grid(start, 12)
        after = _grid(before[-1] + timedelta(hours=3), 12)
        path = _write_series(tmp_path, before + after)

        report = SignalCoverageReport('crypto_sentiment', 'BTCUSD')
        report.analyze([path])

        # Window ends before the gap closes → gap not fully contained
        early_end = datetime(2026, 7, 22, 2, 0, tzinfo=timezone.utc)
        assert report.gaps_in_window(start, early_end) == []

        late_end = datetime(2026, 7, 22, 8, 0, tzinfo=timezone.utc)
        assert len(report.gaps_in_window(start, late_end)) == 1

    def test_coverage_ratio_reflects_the_hole(self, tmp_path):
        start = datetime(2026, 7, 22, 0, 0, tzinfo=timezone.utc)
        before = _grid(start, 6)          # 0:00 → 0:50
        after = _grid(start + timedelta(hours=2), 6)   # 2:00 → 2:50
        path = _write_series(tmp_path, before + after)

        report = SignalCoverageReport('crypto_sentiment', 'BTCUSD')
        report.analyze([path])

        window_end = datetime(2026, 7, 22, 2, 50, tzinfo=timezone.utc)
        ratio = report.coverage_ratio_in_window(start, window_end)
        # ~70min hole inside a 170min window
        assert ratio == pytest.approx(1 - 70 / 170, abs=0.01)


class TestDataOrigin:
    """
    The mock-versus-real discriminator. Absence is 'unknown', never an
    assertion of realness — archives produced before the field exists carry
    no column at all and must still read.
    """

    def _report(self, tmp_path, origin) -> SignalCoverageReport:
        start = datetime(2026, 7, 22, 0, 0, tzinfo=timezone.utc)
        path = _write_series(tmp_path, _grid(start, 12), origin=origin)
        report = SignalCoverageReport('crypto_sentiment', 'BTCUSD')
        report.analyze([path])
        return report

    def test_synthetic_is_detected(self, tmp_path):
        report = self._report(tmp_path, 'synthetic')
        assert report.get_data_origin() == 'synthetic'
        assert report.is_synthetic()

    def test_live_is_not_synthetic(self, tmp_path):
        report = self._report(tmp_path, 'live')
        assert report.get_data_origin() == 'live'
        assert not report.is_synthetic()

    def test_missing_column_reads_as_unknown(self, tmp_path):
        # A parquet written before the field existed — must not raise
        report = self._report(tmp_path, None)
        assert report.get_data_origin() == ''
        assert not report.is_synthetic()
        assert report.snapshot_count == 12

    def test_empty_value_reads_as_unknown(self, tmp_path):
        # Column present, producer did not stamp it
        report = self._report(tmp_path, '')
        assert report.get_data_origin() == ''
        assert not report.is_synthetic()

    def test_mixed_origins_are_flagged(self, tmp_path):
        start = datetime(2026, 7, 22, 0, 0, tzinfo=timezone.utc)
        a = _write_series(tmp_path, _grid(start, 6), name='a', origin='synthetic')
        b = _write_series(tmp_path, _grid(start + CADENCE * 6, 6), name='b', origin='live')

        report = SignalCoverageReport('crypto_sentiment', 'BTCUSD')
        report.analyze([a, b])

        assert report.get_data_origin() == 'mixed'
        assert report.is_synthetic()

    def test_report_text_marks_synthetic(self, tmp_path):
        assert 'SYNTHETIC' in self._report(tmp_path, 'synthetic').generate_report()

    def test_report_text_marks_unknown(self, tmp_path):
        assert 'unknown' in self._report(tmp_path, None).generate_report()


class TestEmptySource:
    """An unimported source degrades cleanly rather than raising."""

    def test_no_paths_yields_empty_report(self):
        report = SignalCoverageReport('unknown_source', 'BTCUSD')
        report.analyze([])

        assert report.snapshot_count == 0
        assert report.start_time is None
        assert not report.has_issues()
        assert not report.has_snapshot_at_or_before(
            datetime(2026, 7, 22, tzinfo=timezone.utc))

    def test_report_text_states_the_absence(self):
        report = SignalCoverageReport('unknown_source', 'BTCUSD')
        report.analyze([])

        assert 'no snapshots' in report.generate_report()

    def test_single_snapshot_keeps_default_cadence(self, tmp_path):
        path = _write_series(
            tmp_path, [datetime(2026, 7, 22, tzinfo=timezone.utc)])

        report = SignalCoverageReport('crypto_sentiment', 'BTCUSD')
        report.analyze([path])

        assert report.snapshot_count == 1
        assert report.gaps == []
