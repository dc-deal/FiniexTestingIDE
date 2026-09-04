"""
Run-summary builder (#390 prework) — the cross-section KPI postprocessor.

Composes the run-wide KPI model from the already-built section reports (portfolio roll-up +
trade analytics + execution totals) — it does NOT re-derive. Per-currency KPIs join the
portfolio aggregate with the trade analytics by currency; the order counts are the global
execution totals. The single object every consumer reads (sweep objective, console headline,
API, live snapshot).
"""

from typing import Dict, Optional

from python.framework.reporting.builders.report_aggregators import aggregate_signal_fresh_ratio
from python.framework.types.api.report_types import (
    ExecutionStatsReport,
    FeedStabilityReport,
    PortfolioAggregateRow,
    PortfolioReport,
    RunSummary,
    RunSummaryCurrency,
    SignalReport,
    TradeAnalytics,
    TradeHistoryReport,
)


def build_run_summary(
    run_id: str,
    portfolio_report: PortfolioReport,
    trade_report: TradeHistoryReport,
    execution_report: ExecutionStatsReport,
    signal_report: Optional[SignalReport] = None,
    feed_stability_report: Optional[FeedStabilityReport] = None,
) -> RunSummary:
    """
    Compose the run-wide KPI summary from the section reports.

    Args:
        run_id: The run this report belongs to
        portfolio_report: The portfolio report (per-currency aggregates)
        trade_report: The trade-history report (per-currency analytics)
        execution_report: The execution-stats report (global order totals)
        signal_report: The signal report (#433) — supplies the run's weakest fresh ratio;
            None / no SIGNAL worker leaves the ratio unset
        feed_stability_report: The feed-stability report (#451) — supplies the run's
            disturbance totals for the executive line

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
        run_id=run_id,
        currencies=currencies,
        orders_sent=totals.orders_sent,
        orders_executed=totals.orders_executed,
        orders_rejected=totals.orders_rejected,
        sl_tp_triggered=totals.sl_tp_triggered,
        unit_count=len(portfolio_report.units),
        signal_fresh_ratio=(
            aggregate_signal_fresh_ratio(signal_report) if signal_report else None),
        disturbance_episode_count=(
            feed_stability_report.episode_count if feed_stability_report else 0),
        disturbance_stale_seconds=(
            feed_stability_report.stale_seconds if feed_stability_report else 0.0),
        disturbance_source_count=(
            feed_stability_report.source_count if feed_stability_report else 0),
        disturbance_stress_injected=(
            feed_stability_report.stress_injected_count if feed_stability_report else 0),
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
        unrealized_pnl=agg.unrealized_pnl,
        final_equity=agg.final_equity,
        open_position_count=agg.open_position_count,
        total_trades=agg.total_trades,
        winning_trades=agg.winning_trades,
        losing_trades=agg.losing_trades,
        expectancy=analytics.expectancy if analytics else 0.0,
        avg_win_r=analytics.avg_win_r if analytics else None,
        avg_loss_r=analytics.avg_loss_r if analytics else None,
        r_trade_count=analytics.r_trade_count if analytics else 0,
        r_win_count=analytics.r_win_count if analytics else 0,
        r_loss_count=analytics.r_loss_count if analytics else 0,
    )
