"""
Operator Alert Messages — what the operator reads, rendered from what the session decided (#235).

The closing message quotes the run's own grade and the figures the report built; it computes
nothing. The two maps it is built on are declared beside their enums, so a new level or a new
grade cannot arrive without an answer here.
"""

from datetime import date, datetime, timezone
from typing import Optional

import pytest

from python.framework.autotrader.operator_alerts.operator_alert_messages import (
    END_LEVEL_BY_OUTCOME,
    LEVEL_MARK,
    day_closed_text,
    format_duration,
    session_ended_text,
    session_started_text,
)
from python.framework.types.api.report_types import RunSummary, RunSummaryCurrency
from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig
from python.framework.types.autotrader_types.autotrader_result_types import AutoTraderResult
from python.framework.types.autotrader_types.operator_alert_types import OperatorAlertLevel
from python.framework.types.run_outcome_types import RunOutcome
from python.framework.types.run_results_types import BookingPeriod, PeriodCloseReason


_DAY_OPENED = datetime(2026, 10, 20, tzinfo=timezone.utc)


def _summary(net_pnl: float, trades: int, unrealized: float = 0.0) -> RunSummary:
    return RunSummary(run_id='r', currencies=[RunSummaryCurrency(
        currency='USD', net_pnl=net_pnl, profit_factor=None, win_rate=0.0,
        account_max_drawdown=0.0, total_fees=0.0, total_trades=trades, winning_trades=0,
        losing_trades=0, expectancy=0.0, avg_win_r=0.0, avg_loss_r=0.0, r_trade_count=0,
        unrealized_pnl=unrealized)])


def _period(opening: Optional[float] = 1_000.0, closing: Optional[float] = 1_012.4,
            unrealized: float = 0.0) -> BookingPeriod:
    figures = RunSummaryCurrency(
        currency='USD', net_pnl=12.4, profit_factor=None, win_rate=0.0, account_max_drawdown=0.0,
        total_fees=1.85, total_trades=3, winning_trades=2, losing_trades=1, expectancy=0.0,
        avg_win_r=None, avg_loss_r=None, r_trade_count=0, unrealized_pnl=unrealized,
        final_equity=closing)
    return BookingPeriod(
        period_no=15, unit_name='eth_daily',
        opened_at=datetime(2026, 10, 20, tzinfo=timezone.utc),
        closed_at=datetime(2026, 10, 21, tzinfo=timezone.utc),
        reason=PeriodCloseReason.ANCHOR, trade_count=3, figures=figures,
        period_max_equity=1_020.0, period_min_equity=995.0, period_max_drawdown=8.2,
        period_opening_equity=opening)


class TestTheDeclaredMaps:

    def test_every_level_has_a_mark(self):
        assert set(LEVEL_MARK) == set(OperatorAlertLevel)

    def test_every_grade_has_a_level(self):
        assert set(END_LEVEL_BY_OUTCOME) == set(RunOutcome)


class TestTheClosingMessage:

    def test_a_clean_end_quotes_the_grade_and_the_reports_figures(self):
        level, title, detail = session_ended_text(
            AutoTraderResult(shutdown_mode='normal', session_duration_s=187_920.0,
                             ticks_processed=31_204),
            _summary(-12.4, 6))
        assert level is OperatorAlertLevel.INFO
        assert title == 'Session ended — SUCCESS (exit 0)'
        assert detail == ('Duration: 2d 4h 12m\n'
                          'Ticks: 31,204\n'
                          'Trades: 6\n'
                          'Net P&L: -12.40 USD')

    def test_a_failed_end_is_critical_and_says_why(self):
        """A hard stop ends through a session-end request — its reason is the executor's."""
        level, title, detail = session_ended_text(
            AutoTraderResult(shutdown_mode='emergency'), _summary(0.0, 0),
            end_reason='emergency flatten incomplete (max_drawdown)')
        assert level is OperatorAlertLevel.CRITICAL
        assert title == 'Session ended — FAILED (exit 2)'
        assert detail.splitlines()[0] == 'Reason: emergency flatten incomplete (max_drawdown)'

    def test_an_open_holding_is_shown_beside_the_realised_figure(self):
        """Realised and valued are kept apart (#492) — a kept holding's loss is not hidden."""
        _, _, detail = session_ended_text(
            AutoTraderResult(shutdown_mode='normal'), _summary(-0.5, 1, unrealized=-412.3))
        assert 'Net P&L: -0.50 USD\nOpen P&L: -412.30 USD' in detail

    def test_an_operator_stop_reads_as_one(self):
        level, title, _ = session_ended_text(
            AutoTraderResult(shutdown_mode='emergency', operator_interrupted=True), None)
        assert level is OperatorAlertLevel.INFO
        assert title.startswith('Session stopped by the operator')

    def test_missing_figures_are_said_rather_than_shown_as_zero(self):
        _, _, detail = session_ended_text(AutoTraderResult(shutdown_mode='normal'), None)
        assert 'Figures: unavailable' in detail
        assert 'P&L' not in detail


class TestTheStartMessage:

    @pytest.mark.parametrize('adapter, dry_run, mode', [
        ('mock', True, 'mock replay'),
        ('kraken', True, 'dry run'),
        ('kraken', False, 'LIVE — real orders'),
    ])
    def test_it_says_whether_real_orders_go_out(self, adapter, dry_run, mode):
        title, detail = session_started_text(
            AutoTraderConfig(profile_name='eth_daily', adapter_type=adapter), dry_run,
            '20261020_070000_a1b2c3d4', '4c61dc93deadbeef')
        assert title == 'Session started'
        assert detail.startswith(mode)
        assert 'Commit: 4c61dc93' in detail and 'deadbeef' not in detail


@pytest.mark.parametrize('seconds, text', [
    (5, '5s'), (725, '12m 5s'), (15_120, '4h 12m'), (187_920, '2d 4h 12m')])
def test_a_duration_reads_the_way_a_person_says_it(seconds, text):
    assert format_duration(seconds) == text


class TestTheDayClose:
    """The sealed booking period, rendered — every figure the period's own (#235)."""

    def test_a_day_the_session_started_in_says_it_covers_only_the_rest(self):
        """A restart at 14:32 seals that day with the figures since 14:32, not the whole day."""
        period = _period()
        period.opened_at = datetime(2026, 10, 20, 14, 32, tzinfo=timezone.utc)
        _, detail = day_closed_text(period, date(2026, 10, 20), _DAY_OPENED)
        assert detail.splitlines()[0] == ('Covers: from 14:32 UTC — the session started '
                                          'during the day')

    def test_a_whole_day_says_nothing_about_coverage(self):
        _, detail = day_closed_text(_period(), date(2026, 10, 20), _DAY_OPENED)
        assert 'Covers' not in detail

    def test_a_closed_day_reads_its_period_and_computes_nothing(self):
        title, detail = day_closed_text(_period(), date(2026, 10, 20), _DAY_OPENED)

        assert title == 'Day closed — 2026-10-20'
        assert detail == ('Trades: 3\n'
                          'Net P&L: +12.40 USD\n'
                          'Fees: 1.85 USD\n'
                          'Equity: 1,000.00 → 1,012.40 USD\n'
                          'Deepest dip: -8.20 USD')

    def test_an_open_holding_is_shown_beside_the_realised_figure(self):
        _, detail = day_closed_text(_period(unrealized=4.1), date(2026, 10, 20), _DAY_OPENED)
        assert 'Net P&L: +12.40 USD\nOpen P&L: +4.10 USD' in detail

    def test_a_value_that_was_not_observed_says_so(self):
        _, detail = day_closed_text(
            _period(opening=None, closing=None), date(2026, 10, 20), _DAY_OPENED)
        assert 'Equity: not observed' in detail

    def test_one_end_observed_says_which(self):
        _, detail = day_closed_text(_period(opening=None), date(2026, 10, 20), _DAY_OPENED)
        assert 'Equity: 1,012.40 USD at the close, the open was not observed' in detail

    def test_a_day_that_never_fell_reads_zero(self):
        period = _period()
        for dip in (0.0, 0.004):
            period.period_max_drawdown = dip
            _, detail = day_closed_text(period, date(2026, 10, 20), _DAY_OPENED)
            assert detail.endswith('Deepest dip: 0.00 USD')
