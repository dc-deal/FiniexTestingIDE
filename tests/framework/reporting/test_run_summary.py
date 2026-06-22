"""
Run-Summary Builder Tests (#390 prework).

Composes the cross-section KPI model from the section reports (portfolio aggregates +
trade analytics + execution totals) — no re-derivation. Per-currency join + global counts.
"""

from python.framework.reporting.builders.run_summary_builder import build_run_summary
from python.framework.types.api.report_types import (
    ExecutionStatsReport, ExecutionStatsTotals, PortfolioAggregateRow, PortfolioReport,
    PortfolioUnitRow, TradeAnalytics, TradeHistoryReport)


def _agg(currency='USD', net=60.0) -> PortfolioAggregateRow:
    return PortfolioAggregateRow(
        currency=currency, unit_count=1, total_trades=10, winning_trades=6, losing_trades=4,
        win_rate=0.6, profit_factor=2.5, total_profit=100.0, total_loss=40.0,
        net_profit=net, max_drawdown=12.0, total_fees=5.0)


def _unit(currency='USD') -> PortfolioUnitRow:
    return PortfolioUnitRow(
        name='s1', symbol='EURUSD', currency=currency, total_trades=10, winning_trades=6,
        losing_trades=4, win_rate=0.6, profit_factor=2.5, total_profit=100.0, total_loss=40.0,
        net_profit=60.0, max_drawdown=12.0, total_fees=5.0)


def _analytics(currency='USD', expectancy=0.5) -> TradeAnalytics:
    return TradeAnalytics(
        currency=currency, trade_count=4, expectancy=expectancy, avg_win_r=2.0,
        avg_loss_r=-1.0, r_trade_count=4, avg_mae_winners=0.0, avg_mae_losers=0.0,
        avg_mfe_losers=0.0)


def _exec(sent=5, ex=4, rej=1, sltp=2) -> ExecutionStatsReport:
    return ExecutionStatsReport(units=[], totals=ExecutionStatsTotals(
        orders_sent=sent, orders_executed=ex, orders_rejected=rej, sl_tp_triggered=sltp))


class TestBuild:
    def test_composes_per_currency(self):
        portfolio = PortfolioReport(units=[_unit()], aggregates=[_agg()])
        trade = TradeHistoryReport(trades=[], count=0, symbols=[], analytics=[_analytics()])
        rs = build_run_summary(portfolio, trade, _exec())
        assert len(rs.currencies) == 1
        c = rs.currencies[0]
        assert c.currency == 'USD'
        assert (c.net_pnl, c.profit_factor, c.win_rate) == (60.0, 2.5, 0.6)
        assert (c.max_drawdown, c.total_fees) == (12.0, 5.0)
        assert (c.expectancy, c.avg_win_r, c.r_trade_count) == (0.5, 2.0, 4)
        assert (rs.orders_sent, rs.orders_executed, rs.sl_tp_triggered) == (5, 4, 2)
        assert rs.unit_count == 1

    def test_currency_without_analytics_defaults_r(self):
        # portfolio currency present, no matching trade analytics → R fields default 0
        portfolio = PortfolioReport(units=[_unit('JPY')], aggregates=[_agg(currency='JPY')])
        trade = TradeHistoryReport(trades=[], count=0, symbols=[], analytics=[])
        c = build_run_summary(portfolio, trade, _exec()).currencies[0]
        assert c.currency == 'JPY' and c.net_pnl == 60.0
        assert c.expectancy == 0.0 and c.r_trade_count == 0

    def test_multi_currency(self):
        portfolio = PortfolioReport(
            units=[_unit('USD'), _unit('JPY')],
            aggregates=[_agg('USD', net=60.0), _agg('JPY', net=100.0)])
        trade = TradeHistoryReport(
            trades=[], count=0, symbols=[],
            analytics=[_analytics('USD'), _analytics('JPY', expectancy=1.0)])
        rs = build_run_summary(portfolio, trade, _exec())
        by = {c.currency: c for c in rs.currencies}
        assert by['USD'].net_pnl == 60.0 and by['JPY'].net_pnl == 100.0
        assert by['JPY'].expectancy == 1.0
        assert rs.unit_count == 2
