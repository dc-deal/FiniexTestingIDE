"""
Run-summary builder (#390 prework) — the cross-section KPI postprocessor.

Composes the run-wide KPI model from the already-built section reports (portfolio roll-up +
trade analytics + execution totals) — it does NOT re-derive. Per-currency KPIs join the
portfolio aggregate with the trade analytics by currency; the order counts are the global
execution totals. The single object every consumer reads (sweep objective, console headline,
API, live snapshot).
"""

from typing import Dict, Optional

from python.framework.types.api.report_types import (
    ExecutionStatsReport, PortfolioAggregateRow, PortfolioReport, RunSummary,
    RunSummaryCurrency, TradeAnalytics, TradeHistoryReport)


def build_run_summary(
    portfolio_report: PortfolioReport,
    trade_report: TradeHistoryReport,
    execution_report: ExecutionStatsReport,
) -> RunSummary:
    """
    Compose the run-wide KPI summary from the section reports.

    Args:
        portfolio_report: The portfolio report (per-currency aggregates)
        trade_report: The trade-history report (per-currency analytics)
        execution_report: The execution-stats report (global order totals)

    Returns:
        RunSummary with one KPI row per currency + the global order counts
    """
    analytics_by_ccy: Dict[str, TradeAnalytics] = {
        a.currency: a for a in trade_report.analytics}
    currencies = [
        _to_currency(agg, analytics_by_ccy.get(agg.currency))
        for agg in portfolio_report.aggregates
    ]
    totals = execution_report.totals
    return RunSummary(
        currencies=currencies,
        orders_sent=totals.orders_sent,
        orders_executed=totals.orders_executed,
        orders_rejected=totals.orders_rejected,
        sl_tp_triggered=totals.sl_tp_triggered,
        unit_count=len(portfolio_report.units),
    )


def _to_currency(
    agg: PortfolioAggregateRow, analytics: Optional[TradeAnalytics]) -> RunSummaryCurrency:
    """Join one currency's portfolio aggregate + trade analytics into a KPI row."""
    return RunSummaryCurrency(
        currency=agg.currency,
        net_pnl=agg.net_profit,
        profit_factor=agg.profit_factor,
        win_rate=agg.win_rate,
        max_drawdown=agg.max_drawdown,
        total_fees=agg.total_fees,
        total_trades=agg.total_trades,
        winning_trades=agg.winning_trades,
        losing_trades=agg.losing_trades,
        expectancy=analytics.expectancy if analytics else 0.0,
        avg_win_r=analytics.avg_win_r if analytics else 0.0,
        avg_loss_r=analytics.avg_loss_r if analytics else 0.0,
        r_trade_count=analytics.r_trade_count if analytics else 0,
    )
