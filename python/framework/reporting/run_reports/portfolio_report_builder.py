"""
Portfolio report builder (#391) — the headline-P&L + full-projection postprocessor.

The unified array model: a run is a list of units (sim: N scenarios; live: 1 session) plus a
per-currency roll-up. Consumes the run's `RunUnit` list and maps each unit's `PortfolioStats`
to the full per-unit projection (the per-scenario linear console block renders purely from it).
The per-currency roll-up is derived from the rows via the shared `aggregate_portfolio_by_currency`
(one builder for both pipelines; mirrors the console `PortfolioAggregator` formulas).
"""

from typing import List

from python.framework.reporting.run_reports.report_aggregators import aggregate_portfolio_by_currency
from python.framework.reporting.run_reports.run_unit import RunUnit
from python.framework.types.api.report_types import PortfolioReport, PortfolioUnitRow


def build_portfolio_report(units: List[RunUnit]) -> PortfolioReport:
    """
    Build the portfolio report from the run's units (per-unit rows + per-currency roll-up).

    Args:
        units: The run's units (sim: N scenarios; live: 1 session)

    Returns:
        PortfolioReport with one full-projection row per unit (with stats) + per-currency aggregate
    """
    rows = [_to_unit_row(u) for u in units if u.portfolio_stats is not None]
    return PortfolioReport(units=rows, aggregates=aggregate_portfolio_by_currency(rows))


def _to_unit_row(unit: RunUnit) -> PortfolioUnitRow:
    """Map a unit's portfolio stats to the full per-unit projection row."""
    stats = unit.portfolio_stats
    return PortfolioUnitRow(
        name=unit.name,
        symbol=unit.symbol,
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
        data_source=unit.data_source,
        broker_name=stats.broker_name,
        spot_mode=stats.spot_mode,
        total_long_trades=stats.total_long_trades,
        total_short_trades=stats.total_short_trades,
        max_equity=stats.max_equity,
        current_balance=stats.current_balance,
        initial_balance=stats.initial_balance,
        conversion_rate=stats.current_conversion_rate,
        total_spread_cost=stats.total_spread_cost,
        total_commission=stats.total_commission,
        total_swap=stats.total_swap,
        has_error=unit.has_error,
        balances=stats.balances,
        initial_balances=stats.initial_balances,
        last_price=stats.last_price,
    )
