"""
Portfolio report builder (#391) — the headline-P&L postprocessor.

The unified array model: a run is a list of units (sim: N scenarios; live: 1
session) plus a per-currency roll-up. Two source builders feed one model, both pure
and fixture-testable. The sim aggregate is injected (the persist site computes it via
the existing `PortfolioAggregator`) so this module stays in its layer — no reach-up
to the console-summary land, no re-derivation, no divergence from the console total.
"""

from typing import Dict, List

from python.framework.types.api.report_types import (
    PortfolioAggregateRow, PortfolioReport, PortfolioUnitRow)
from python.framework.types.autotrader_types.autotrader_result_types import AutoTraderResult
from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.types.portfolio_types.portfolio_aggregation_types import (
    AggregatedPortfolio, BasePortfolioStats)


def build_portfolio_report_from_batch(
    batch: BatchExecutionSummary,
    currency_aggregates: Dict[str, AggregatedPortfolio],
) -> PortfolioReport:
    """
    Build the portfolio report from a sim batch (N scenario units + per-currency roll-up).

    Args:
        batch: The completed batch summary (scenarios = units)
        currency_aggregates: Per-currency aggregation (injected, built by the persist
            site via PortfolioAggregator — kept out of this layer)

    Returns:
        PortfolioReport with one unit per scenario and one aggregate per currency
    """
    units: List[PortfolioUnitRow] = []
    for result in batch.process_result_list:
        tick_loop = getattr(result, 'tick_loop_results', None)
        if not tick_loop or tick_loop.portfolio_stats is None:
            continue
        # The symbol is scenario identity (ProcessResult carries none) — pull it from
        # the index-synced SingleScenario.
        scenario = batch.get_scenario_by_process_result(result)
        units.append(_to_unit_row(
            result.scenario_name, scenario.symbol, tick_loop.portfolio_stats))

    aggregates = [
        _to_aggregate_row(agg.currency, agg.scenario_count, agg.portfolio_stats)
        for agg in currency_aggregates.values()
    ]
    return PortfolioReport(units=units, aggregates=aggregates)


def build_portfolio_report_from_session(
    session: AutoTraderResult,
    name: str,
    symbol: str,
) -> PortfolioReport:
    """
    Build the portfolio report from a live session (1 unit = the aggregate).

    Args:
        session: The collected session result
        name: Unit label (profile name / symbol)
        symbol: Traded symbol

    Returns:
        PortfolioReport with the single session unit + its currency aggregate (empty
        if the session produced no portfolio stats)
    """
    stats = session.portfolio_stats
    if stats is None:
        return PortfolioReport(units=[], aggregates=[])
    return PortfolioReport(
        units=[_to_unit_row(name, symbol, stats)],
        aggregates=[_to_aggregate_row(stats.currency, 1, stats)],
    )


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


def _to_aggregate_row(
    currency: str, unit_count: int, stats: BasePortfolioStats) -> PortfolioAggregateRow:
    """Map a currency-group aggregate to a headline row."""
    return PortfolioAggregateRow(
        currency=currency,
        unit_count=unit_count,
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
