"""
Operator Alert Messages — what the operator reads, rendered from what the session decided (#235).

The closing message quotes the run's own grade and the figures the report built; it computes
nothing. The two maps it is built on are declared beside their enums, so a new level or a new
grade cannot arrive without an answer here.
"""

import pytest

from python.framework.autotrader.operator_alerts.operator_alert_messages import (
    END_LEVEL_BY_OUTCOME,
    LEVEL_MARK,
    format_duration,
    session_ended_text,
    session_started_text,
)
from python.framework.types.api.report_types import RunSummary, RunSummaryCurrency
from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig
from python.framework.types.autotrader_types.autotrader_result_types import AutoTraderResult
from python.framework.types.autotrader_types.operator_alert_types import OperatorAlertLevel
from python.framework.types.run_outcome_types import RunOutcome


def _summary(net_pnl: float, trades: int, unrealized: float = 0.0) -> RunSummary:
    return RunSummary(run_id='r', currencies=[RunSummaryCurrency(
        currency='USD', net_pnl=net_pnl, profit_factor=None, win_rate=0.0,
        account_max_drawdown=0.0, total_fees=0.0, total_trades=trades, winning_trades=0,
        losing_trades=0, expectancy=0.0, avg_win_r=0.0, avg_loss_r=0.0, r_trade_count=0,
        unrealized_pnl=unrealized)])


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
        assert detail == '2d 4h 12m · 31,204 ticks · 6 trade(s) · net P&L -12.40 USD'

    def test_a_failed_end_is_critical_and_says_why(self):
        """A hard stop ends through a session-end request — its reason is the executor's."""
        level, title, detail = session_ended_text(
            AutoTraderResult(shutdown_mode='emergency'), _summary(0.0, 0),
            end_reason='emergency flatten incomplete (max_drawdown)')
        assert level is OperatorAlertLevel.CRITICAL
        assert title == 'Session ended — FAILED (exit 2)'
        assert detail.splitlines()[0] == 'emergency flatten incomplete (max_drawdown)'

    def test_an_open_holding_is_shown_beside_the_realised_figure(self):
        """Realised and valued are kept apart (#492) — a kept holding's loss is not hidden."""
        _, _, detail = session_ended_text(
            AutoTraderResult(shutdown_mode='normal'), _summary(-0.5, 1, unrealized=-412.3))
        assert 'net P&L -0.50 USD · open -412.30 USD' in detail

    def test_an_operator_stop_reads_as_one(self):
        level, title, _ = session_ended_text(
            AutoTraderResult(shutdown_mode='emergency', operator_interrupted=True), None)
        assert level is OperatorAlertLevel.INFO
        assert title.startswith('Session stopped by the operator')

    def test_missing_figures_are_said_rather_than_shown_as_zero(self):
        _, _, detail = session_ended_text(AutoTraderResult(shutdown_mode='normal'), None)
        assert 'figures unavailable' in detail
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
        assert 'commit 4c61dc93' in detail and 'deadbeef' not in detail


@pytest.mark.parametrize('seconds, text', [
    (5, '5s'), (725, '12m 5s'), (15_120, '4h 12m'), (187_920, '2d 4h 12m')])
def test_a_duration_reads_the_way_a_person_says_it(seconds, text):
    assert format_duration(seconds) == text
