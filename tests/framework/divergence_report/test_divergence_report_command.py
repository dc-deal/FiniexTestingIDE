"""
The divergence report as one command (#512): the steps that join the building blocks — the check
that the archive covers a window before its backtest, finding the backtest's run by its set's name,
a capture difference confirmed through the venue's trades and the archive's ids, and the report
kept and printed.
"""

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from python.configuration.app_config_manager import AppConfigManager
from python.framework.reporting.builders.divergence.venue_side_collector import (
    VenueSideCollector,
)
from python.framework.reporting.console.divergence_report_console import render_divergence_report
from python.framework.reporting.divergence_report_coordinator import (
    DivergenceReportCoordinator,
    newest_run_named,
    preflight,
)
from python.framework.reporting.store.divergence_report_store import (
    divergence_root,
    new_report_directory,
    write_report,
)
from python.framework.types.api.divergence_report_types import (
    CompareWindow,
    DayDecision,
    DecisionComparison,
    DecisionComparisonState,
    DivergenceReading,
    DivergenceReport,
    ExplainedFlipKind,
    LiveSegment,
    LiveSide,
    SegmentMeasures,
    SegmentReport,
    WindowEndCause,
)
from python.framework.types.divergence_capture_types import DerivedSegment
from python.framework.types.venue_history_types import VenueTrade
from python.framework.venue_history.abstract_public_trade_reader import (
    AbstractPublicTradeReader,
)
from tests.shared.null_logger import NullLogger

_UTC = timezone.utc
_BAR = datetime(2026, 11, 25, tzinfo=_UTC)


def _derived(start, end) -> DerivedSegment:
    return DerivedSegment(index=1, started_by=[], sessions=[], ends=[], declared_stops=[],
                          window=CompareWindow(start=start, end=end,
                                               end_cause=WindowEndCause.UNTIL))


class TestTheArchiveCoversTheWindow:

    def test_a_window_past_the_archive_waits_for_the_import(self):
        refusal = preflight(_derived(datetime(2099, 1, 1, tzinfo=_UTC),
                                      datetime(2099, 1, 2, tzinfo=_UTC)), 'kraken_spot', 'ETHUSD')
        assert 'not past the window' in refusal and 'wait for the import' in refusal

    def test_a_window_inside_the_archive_may_be_backtested(self):
        assert preflight(_derived(datetime(2026, 1, 1, tzinfo=_UTC),
                                   datetime(2026, 1, 2, tzinfo=_UTC)), 'kraken_spot',
                          'ETHUSD') == ''

    def test_a_window_without_a_start_is_named(self):
        assert 'has no start' in preflight(_derived(None, None), 'kraken_spot', 'ETHUSD')

    def test_a_symbol_the_archive_lacks_is_named(self):
        assert 'holds no MYSYMBOL ticks' in preflight(
            _derived(datetime(2026, 1, 1, tzinfo=_UTC), datetime(2026, 1, 2, tzinfo=_UTC)),
            'kraken_spot', 'MYSYMBOL')


def test_the_backtest_is_found_by_its_sets_name():
    index_path = Path(AppConfigManager().get_file_logging_config_object().run_index)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    rows = pd.DataFrame({'run_id': ['r_old', 'r_new', 'r_other'],
                         'run_name': ['divergence_x_segment_1'] * 2 + ['my_set'],
                         'start_time': ['2026-11-10T09:00:00+00:00', '2026-11-10T10:00:00+00:00',
                                        '2026-11-10T11:00:00+00:00']})
    if index_path.exists():
        rows = pd.concat([pd.read_parquet(index_path), rows], ignore_index=True)
    rows.to_parquet(index_path)
    assert newest_run_named('divergence_x_segment_1') == 'r_new'
    assert newest_run_named('divergence_nobody_segment_1') is None


class _Trades(AbstractPublicTradeReader):
    """The venue's trades of one bar."""

    def read_trades(self, symbol, start, end):
        return [VenueTrade(trade_id=n, executed_at=start + timedelta(minutes=n), price=3000.0,
                           volume=0.01) for n in (1, 2, 3)]


class _TickIndex:
    """A tick index handing out one file holding trades 1 and 2 of the bar."""

    def __init__(self, path: Path):
        msc = [int((_BAR - timedelta(days=1) + timedelta(minutes=n)).timestamp() * 1000)
               for n in (1, 2)]
        pd.DataFrame({'time_msc': msc, 'bid': [3000.0] * 2, 'ask': [3000.5] * 2,
                      'trade_id': pd.array([1, 2], dtype='Int64')}).to_parquet(path)
        self._path = path

    def get_relevant_files(self, broker_type, symbol, start, end):
        return [self._path]


def test_a_capture_difference_is_confirmed_by_the_trades_the_archive_lacks(tmp_path):
    at = _BAR + timedelta(seconds=1)
    capture = DecisionComparison(
        trading_day=date(2026, 11, 25), closing=_BAR + timedelta(days=1),
        state=DecisionComparisonState.ONE_SIDED, proposed_kind=ExplainedFlipKind.CAPTURE,
        live=DayDecision(frame_time=at, decision_bar_open=_BAR - timedelta(days=1)),
        backtest=DayDecision(frame_time=at, decision_bar_open=_BAR - timedelta(days=1)),
        detail="workers differ · unconfirmed until the venue's trades show it")
    measures = SegmentMeasures(segment_index=1, scenario='segment_1', decisions=[capture])
    coordinator = DivergenceReportCoordinator('my_deployment', 'D1', DivergenceReading.RUN,
                                              NullLogger())
    venue = VenueSideCollector('kraken_spot', _Trades(), None)
    confirmed, unread = coordinator.confirm_captures(
        measures, venue, _TickIndex(tmp_path / 'ticks.parquet'), 'kraken_spot', 'ETHUSD')
    answered, = confirmed.decisions
    assert answered.capture_confirmed is True and answered.capture_missing_trades == 1
    assert unread == []


def _report(segment: SegmentReport) -> DivergenceReport:
    return DivergenceReport(
        report_id='my_report', created_at=datetime(2026, 11, 17, 10, tzinfo=_UTC),
        deployment_id='my_deployment', symbol='ETHUSD', decision_timeframe='D1',
        reading=DivergenceReading.REHEARSAL,
        live=LiveSide(deployment_id='my_deployment', broker_type='kraken_spot',
                      anchor_timezone='UTC', anchor_local_time='00:00', segments=[segment.segment]),
        segments=[segment])


def test_a_segment_not_compared_is_printed_with_why_and_kept(tmp_path):
    segment = SegmentReport(
        segment=LiveSegment(index=1, started_by=[], run_ids=['s1'],
                            window=CompareWindow(start=_BAR, end=_BAR + timedelta(days=5),
                                                 end_cause=WindowEndCause.UNTIL)),
        scenario_set_name='divergence_my_report_segment_1',
        not_compared_because='the archive ends 2026-11-29, not past the window\'s end')
    report = _report(segment)
    text = render_divergence_report(report)
    assert 'NOT COMPARED: the archive ends' in text and 'read as rehearsal' in text
    report_dir = new_report_directory()
    write_report(report, text, report_dir)
    assert report_dir.parent == divergence_root()
    assert sorted(p.name for p in report_dir.iterdir()) == ['report.json', 'report.txt']
    assert DivergenceReport.model_validate_json(
        (report_dir / 'report.json').read_text(encoding='utf-8')) == report
