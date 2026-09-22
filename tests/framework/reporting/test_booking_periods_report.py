"""
The Hauptbuch as a table, and the line that makes it trustworthy.

A period summary is believed because it can be recomputed from its records. A COLUMN of period
summaries is believed because it agrees with the figure the run reports by a different route —
the portfolio aggregate. These tests pin that the agreement is computed and stated, and that a
disagreement is reported rather than smoothed over.
"""

from datetime import datetime, timedelta, timezone

from python.framework.reporting.builders.booking_periods_report_builder import (
    build_booking_periods_report,
)
from python.framework.reporting.console.booking_periods_summary import render_booking_periods
from python.framework.reporting.builders.run_unit import RunUnit
from python.framework.types.api.report_types import RunSummary, RunSummaryCurrency
from python.framework.types.run_results_types import BookingSegment, SegmentCloseReason

_MON = datetime(2026, 9, 21, tzinfo=timezone.utc)


def _figures(**overrides) -> RunSummaryCurrency:
    """One currency's KPI row with the fields every case needs."""
    base = dict(
        currency='USD', net_pnl=0.0, profit_factor=None, win_rate=0.0,
        account_max_drawdown=0.0, total_fees=0.0, total_trades=0, winning_trades=0,
        losing_trades=0, expectancy=0.0, avg_win_r=None, avg_loss_r=None, r_trade_count=0)
    base.update(overrides)
    return RunSummaryCurrency(**base)


def _segment(segment_no: int, day: int, net_pnl: float, trades: int,
             currency: str = 'USD',
             reason: SegmentCloseReason = SegmentCloseReason.ANCHOR) -> BookingSegment:
    """One sealed period on day `day` of the week starting Monday."""
    return BookingSegment(
        segment_no=segment_no, unit_name='session',
        opened_at=_MON + timedelta(days=day), closed_at=_MON + timedelta(days=day + 1),
        reason=reason, trade_count=trades,
        figures=_figures(currency=currency, net_pnl=net_pnl, total_trades=trades,
                         final_equity=10_000.0 + net_pnl),
        segment_max_equity=10_050.0, segment_min_equity=9_960.0, segment_max_drawdown=-40.0)


def _units(*segments) -> list:
    """The run's one unit, carrying these periods."""
    return [RunUnit(name='session', symbol='DOTUSD', booking_segments=list(segments))]


def _summary(**overrides) -> RunSummary:
    """The run's own figures — the independent side of the reconciliation."""
    return RunSummary(run_id='r', currencies=[_figures(**overrides)])


class TestTheTableReconciles:

    def test_the_periods_sum_to_the_run(self):
        report = build_booking_periods_report(
            'r', _units(_segment(1, 0, 60.0, 2), _segment(2, 1, -10.0, 2)),
            _summary(net_pnl=50.0, total_trades=4))
        assert report.total_net_pnl == 50.0
        assert report.total_trades == 4
        assert report.reconciles is True

    def test_a_mismatch_is_reported_rather_than_smoothed(self):
        # The finding the table exists to surface: two derivations over the same trades cannot
        # both be right. A trade realised outside every period looks exactly like this.
        report = build_booking_periods_report(
            'r', _units(_segment(1, 0, 60.0, 2)), _summary(net_pnl=75.0, total_trades=3))
        assert report.reconciles is False
        assert report.total_net_pnl == 60.0
        assert report.run_net_pnl == 75.0

    def test_a_rounding_difference_still_counts_as_agreement(self):
        # Thirty additions do not land on the same last bit as one aggregate over the same
        # trades. A table that cried mismatch over 1e-10 would be a table nobody reads.
        report = build_booking_periods_report(
            'r', _units(_segment(1, 0, 60.0, 2)), _summary(net_pnl=60.000000001, total_trades=2))
        assert report.reconciles is True


class TestWhatTheTableShows:

    def test_the_periods_are_ordered_by_unit_then_number(self):
        report = build_booking_periods_report(
            'r', _units(_segment(2, 1, -10.0, 1), _segment(1, 0, 60.0, 2)),
            _summary(net_pnl=50.0, total_trades=3))
        assert [row.segment_no for row in report.periods] == [1, 2]

    def test_the_drawdown_column_is_the_deepest_SINGLE_period(self):
        # Not the run's drawdown: a fall that crosses a period boundary is deeper than any one
        # period's own, and the run summary is where that figure lives.
        report = build_booking_periods_report(
            'r', _units(_segment(1, 0, 60.0, 2), _segment(2, 1, -10.0, 1)),
            _summary(net_pnl=50.0, total_trades=3))
        assert report.deepest_period_drawdown == -40.0

    def test_the_final_equity_is_the_last_period_s(self):
        report = build_booking_periods_report(
            'r', _units(_segment(1, 0, 60.0, 2), _segment(2, 1, -10.0, 1)),
            _summary(net_pnl=50.0, total_trades=3))
        assert report.final_equity == 9_990.0

    def test_the_close_reason_survives_into_the_row(self):
        # Only a `session_end` on the last period says the books are complete.
        report = build_booking_periods_report(
            'r', _units(_segment(1, 0, 60.0, 2, reason=SegmentCloseReason.SESSION_END)),
            _summary(net_pnl=60.0, total_trades=2))
        assert report.periods[0].reason == 'session_end'


class TestWhenThereIsNothingToShow:

    def test_a_run_that_booked_no_period_yields_an_empty_report(self):
        # Every simulation until it books too, and every run written before this feature. An
        # empty table with a heading would read as "this run traded nothing".
        report = build_booking_periods_report('r', _units(), _summary(net_pnl=0.0))
        assert report.periods == []

    def test_currencies_are_never_mixed_into_one_table(self):
        report = build_booking_periods_report(
            'r', _units(_segment(1, 0, 60.0, 2), _segment(1, 0, 0.002, 1, currency='BTC')),
            _summary(net_pnl=60.0, total_trades=2), currency='USD')
        assert {row.currency for row in report.periods} == {'USD'}
        assert report.total_net_pnl == 60.0


class TestManyUnitsCollapse:
    """
    A robustness set books one period per trading day PER SCENARIO. Forty scenarios over
    three-day windows is a hundred and twenty console lines, which pushes the run summary off
    the screen and tells the reader nothing they could act on.

    The threshold is the console's existing `scenario_detail_threshold`, deliberately: this is
    the same question the per-scenario detail already answers, and a second rule would be a
    second answer that drifts from the first.
    """

    @staticmethod
    def _multi_unit(units: int, periods_each: int):
        """A run of `units` scenarios, each booking `periods_each` periods."""
        return [
            RunUnit(name=f'scenario_{u:02d}', symbol='DOTUSD', booking_segments=[
                BookingSegment(
                    segment_no=p + 1, unit_name=f'scenario_{u:02d}',
                    opened_at=_MON + timedelta(days=p), closed_at=_MON + timedelta(days=p + 1),
                    reason=SegmentCloseReason.ANCHOR, trade_count=1,
                    figures=_figures(net_pnl=1.0, total_trades=1, final_equity=10_001.0),
                    segment_max_equity=10_010.0, segment_min_equity=9_990.0,
                    segment_max_drawdown=-20.0)
                for p in range(periods_each)])
            for u in range(units)
        ]

    def test_the_report_itself_is_unchanged_by_the_collapse(self, capsys):
        # The collapse is a RENDERING decision. Every period stays in the report, in the
        # artifact and in the ledger — what changes is only how many lines the console spends.
        units = self._multi_unit(units=40, periods_each=3)
        report = build_booking_periods_report(
            'r', units, _summary(net_pnl=120.0, total_trades=120))
        assert len(report.periods) == 120
        assert report.reconciles is True

    def test_above_the_threshold_one_line_per_unit(self, capsys):
        units = self._multi_unit(units=12, periods_each=3)
        report = build_booking_periods_report(
            'r', units, _summary(net_pnl=36.0, total_trades=36))
        render_booking_periods(report, detail_threshold=9)
        out = capsys.readouterr().out
        # The count is on the heading, so nothing is dropped silently.
        assert '36 period(s) over 12 unit(s)' in out
        assert 'scenario_00' in out and 'scenario_11' in out
        # …and no individual period line survives the collapse.
        assert out.count('scenario_00') == 1

    def test_at_or_below_the_threshold_every_period_is_printed(self, capsys):
        units = self._multi_unit(units=3, periods_each=2)
        report = build_booking_periods_report(
            'r', units, _summary(net_pnl=6.0, total_trades=6))
        render_booking_periods(report, detail_threshold=9)
        out = capsys.readouterr().out
        assert '6 period(s)' in out
        # Three sub-headings, one per unit, and six period lines beneath them.
        assert out.count('▸ scenario_') == 3

    def test_one_unit_gets_no_sub_heading(self, capsys):
        # The live session and the comparison backtest the parity proof uses are both one unit,
        # so the sub-heading would be the same string on every line.
        report = build_booking_periods_report(
            'r', _units(_segment(1, 0, 60.0, 2)), _summary(net_pnl=60.0, total_trades=2))
        render_booking_periods(report, detail_threshold=9)
        assert '▸' not in capsys.readouterr().out
