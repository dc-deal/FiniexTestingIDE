"""
Portfolio report builder (#391) — the headline-P&L postprocessor.

The unified array model: a run is a list of units (sim: N scenarios; live: 1 session) plus a
per-currency roll-up. Consumes the run's `RunUnit` list for the per-unit rows and derives the
roll-up from those rows via the shared `aggregate_portfolio_by_currency` — one builder for
both pipelines (the aggregation groups by currency, so sim and live share it; it mirrors the
console `PortfolioAggregator` formulas so report and console stay identical).
"""

from typing import List

from python.framework.reporting.run_reports.report_aggregators import aggregate_portfolio_by_currency
from python.framework.reporting.run_reports.run_unit import RunUnit
from python.framework.types.api.report_types import PortfolioReport, PortfolioUnitRow
from python.framework.types.portfolio_types.portfolio_aggregation_types import BasePortfolioStats


def build_portfolio_report(units: List[RunUnit]) -> PortfolioReport:
    """
    Build the portfolio report from the run's units (per-unit rows + per-currency roll-up).

    Args:
        units: The run's units (sim: N scenarios; live: 1 session)

    Returns:
        PortfolioReport with one unit row per unit (with stats) and one aggregate per currency
    """
    rows = [
        _to_unit_row(u.name, u.symbol, u.portfolio_stats)
        for u in units if u.portfolio_stats is not None
    ]
    return PortfolioReport(units=rows, aggregates=aggregate_portfolio_by_currency(rows))


def _to_unit_row(name: str, symbol: str, stats: BasePortfolioStats) -> PortfolioUnitRow:
    """Map a unit's portfolio stats to a headline row."""
    return PortfolioUnitRow(
        name=name,
        symbol=symbol,
        currency=stats.currency,
        total_trades=stats.total_trades,
        winning_trades=stats.winning_trades,
        losing_trades=stats.losing_trades,
        win_rate=stats.win_rate,
        profit_factor=stats.profit_factor,
        total_profit=stats.total_profit,
        total_loss=stats.total_loss,
        net_profit=stats.total_profit - stats.total_loss,
        max_drawdown=stats.max_drawdown,
        total_fees=stats.total_fees,
    )
