"""
Portfolio report builder (#391) — the headline-P&L + full-projection postprocessor.

The unified array model: a run is a list of units (sim: N scenarios; live: 1 session) plus a
per-currency roll-up. Consumes the run's `RunUnit` list and maps each unit's `PortfolioStats`
to the full per-unit projection (the per-scenario linear console block renders purely from it).
The per-currency roll-up is derived from the rows via the shared `aggregate_portfolio_by_currency`
(one builder for both pipelines; mirrors the console `PortfolioAggregator` formulas).
"""

from typing import List

from python.framework.reporting.builders.report_aggregators import aggregate_portfolio_by_currency
from python.framework.reporting.builders.run_unit import RunUnit
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


def _spot_estimate(stats) -> tuple:
    """Estimated spot portfolio value from the dual balance (quote + base x last price).

    Args:
        stats: The unit's portfolio stats (carries balances + the stamped currency split)

    Returns:
        (current, initial, pnl, pnl_pct) — all zero when the unit is not spot or has no price
    """
    if not stats.spot_mode or stats.last_price <= 0:
        return 0.0, 0.0, 0.0, 0.0
    quote, base = stats.quote_currency, stats.base_currency
    current = stats.balances.get(quote, 0.0) + stats.balances.get(base, 0.0) * stats.last_price
    initial = (stats.initial_balances.get(quote, 0.0)
               + stats.initial_balances.get(base, 0.0) * stats.last_price)
    pnl = current - initial
    return current, initial, pnl, (pnl / initial * 100) if initial > 0 else 0.0


def _to_unit_row(unit: RunUnit) -> PortfolioUnitRow:
    """Map a unit's portfolio stats to the full per-unit projection row."""
    stats = unit.portfolio_stats
    est_current, est_initial, est_pnl, est_pnl_pct = _spot_estimate(stats)
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
        max_dd_pct=(stats.max_drawdown / stats.max_equity * 100) if stats.max_equity > 0 else 0.0,
        total_fees=stats.total_fees,
        data_source=unit.data_source,
        sentiment_source=unit.sentiment_source,
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
        maker_fee=stats.maker_fee,
        taker_fee=stats.taker_fee,
        has_error=unit.has_error,
        balances=stats.balances,
        initial_balances=stats.initial_balances,
        last_price=stats.last_price,
        base_currency=stats.base_currency,
        quote_currency=stats.quote_currency,
        spot_est_current=est_current,
        spot_est_initial=est_initial,
        spot_est_pnl=est_pnl,
        spot_est_pnl_pct=est_pnl_pct,
    )
