"""
Run-Summary Console Headline Tests (#393 — Executive → RunSummary).

ExecutiveSummary._render_run_summary is a thin presenter over the RunSummary model: it reads
the per-currency KPIs + global order counts and renders the model-fed headline, never
re-deriving. Tested by feeding a hand-built model and asserting the rendered text — the
other executive sub-sections (timing, resources) are not exercised here.
"""

import io
from contextlib import redirect_stdout

from python.framework.batch_reporting.executive_summary import ExecutiveSummary
from python.framework.types.api.report_types import (
    AggregatedPortfolioReport, RunSummary, RunSummaryCurrency, WarningsErrorsReport)
from python.framework.utils.console_renderer import ConsoleRenderer


def _currency(currency='USD', net_pnl=6.0, win_rate=0.6667, winners=2, losers=1,
              profit_factor=4.33, total_fees=3.70, expectancy=0.0, r_trade_count=0):
    return RunSummaryCurrency(
        currency=currency, net_pnl=net_pnl, profit_factor=profit_factor, win_rate=win_rate,
        max_drawdown=1.8, total_fees=total_fees, total_trades=winners + losers,
        winning_trades=winners, losing_trades=losers, expectancy=expectancy,
        avg_win_r=0.0, avg_loss_r=0.0, r_trade_count=r_trade_count)


def _summary(currencies, sent=5, executed=5, rejected=0, sl_tp=0, units=5) -> RunSummary:
    return RunSummary(
        currencies=currencies, orders_sent=sent, orders_executed=executed,
        orders_rejected=rejected, sl_tp_triggered=sl_tp, unit_count=units)


def _render(summary: RunSummary) -> str:
    # Only _render_run_summary is exercised — it reads self._run_summary alone.
    executive = ExecutiveSummary(
        batch_execution_summary=None, app_config=None, run_summary=summary,
        warnings_errors_report=WarningsErrorsReport(),
        aggregated_report=AggregatedPortfolioReport())
    buf = io.StringIO()
    with redirect_stdout(buf):
        executive._render_run_summary(ConsoleRenderer())
    return buf.getvalue()


def test_headline_and_global_counts():
    out = _render(_summary([_currency()]))
    assert 'RUN SUMMARY' in out
    assert 'Scenarios:          5' in out
    assert '5/5 executed' in out


def test_per_currency_kpis():
    out = _render(_summary([_currency(currency='USD', net_pnl=6.0, winners=2, losers=1)]))
    assert 'USD:' in out
    assert 'Win 66.7% (2W/1L)' in out
    assert 'PF 4.33' in out


def test_expectancy_na_without_sl_trades():
    # r_trade_count == 0 → expectancy undefined → 'n/a'
    out = _render(_summary([_currency(r_trade_count=0)]))
    assert 'Exp n/a' in out


def test_expectancy_shown_with_sl_trades():
    out = _render(_summary([_currency(expectancy=0.5, r_trade_count=4)]))
    assert 'Exp +0.50R' in out


def test_rejected_and_sltp_suffixes():
    out = _render(_summary([_currency()], rejected=2, sl_tp=3))
    assert 'rejected' in out
    assert '3 SL/TP' in out


def test_no_currencies_renders_counts_only():
    out = _render(_summary([], units=0))
    assert 'RUN SUMMARY' in out
    assert 'Scenarios:          0' in out
