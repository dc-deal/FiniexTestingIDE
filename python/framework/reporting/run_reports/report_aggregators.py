"""
Report aggregators (#391) — the measures over the per-unit report rows.

One home for every section's aggregation: a pure `aggregate_*(rows) → aggregate(s)` over the
already-mapped model rows (the unified per-unit projection). Keeping the aggregators together
(facts → measures) makes the pattern consistent and is the seam a future run-wide `RunSummary`
composes from (#390 prework). Counts aggregate currency-agnostically; P&L-denominated figures
group per account currency; ratios (win rate / profit factor) are recomputed from the summed
components, never summed.
"""

from typing import Dict, List

from python.framework.types.api.report_types import (
    ExecutionStatsRow, ExecutionStatsTotals, PortfolioAggregateRow, PortfolioUnitRow,
    TradeAnalytics, TradeHistoryRow, TradeScenarioTotals)


# --- Trade analytics (per account currency) -------------------------------------------

def aggregate_trade_analytics(rows: List[TradeHistoryRow]) -> List[TradeAnalytics]:
    """
    Per-currency trade analytics (#389/#393): group the rows by account currency and compute
    one TradeAnalytics each, so the P&L-denominated fields (MAE/MFE) never mix currencies.

    Args:
        rows: The report's trade rows (already filtered)

    Returns:
        One TradeAnalytics per currency present (sorted), [] for no rows
    """
    groups: Dict[str, List[TradeHistoryRow]] = {}
    for row in rows:
        groups.setdefault(row.currency, []).append(row)
    return [_trade_analytics(groups[c]) for c in sorted(groups)]


def _trade_analytics(rows: List[TradeHistoryRow]) -> TradeAnalytics:
    """Aggregate the per-row analytics (#389) for ONE currency group."""
    r_rows = [r for r in rows if r.r_multiple is not None]
    winners = [r for r in rows if r.net_pnl > 0]
    losers = [r for r in rows if r.net_pnl < 0]
    return TradeAnalytics(
        currency=rows[0].currency if rows else '',
        trade_count=len(rows),
        expectancy=_mean([r.r_multiple for r in r_rows]),
        avg_win_r=_mean([r.r_multiple for r in r_rows if r.net_pnl > 0]),
        avg_loss_r=_mean([r.r_multiple for r in r_rows if r.net_pnl < 0]),
        r_trade_count=len(r_rows),
        avg_mae_winners=_mean([r.mae_pnl for r in winners]),
        avg_mae_losers=_mean([r.mae_pnl for r in losers]),
        avg_mfe_losers=_mean([r.mfe_pnl for r in losers]),
        gross_pnl=sum(r.gross_pnl for r in rows),
        net_pnl=sum(r.net_pnl for r in rows),
        total_fees=sum(r.total_fees for r in rows),
    )


# --- Trade per-scenario totals (the per-scenario table footer) ------------------------

def aggregate_trade_scenario_totals(rows: List[TradeHistoryRow]) -> List[TradeScenarioTotals]:
    """
    Per-scenario trade-table totals (the footer line): group the rows by `scenario_name`
    and sum gross / net / fees, so the console (and the API) read the footer off the model.

    Args:
        rows: The report's trade rows (already filtered)

    Returns:
        One TradeScenarioTotals per scenario present (first-appearance order)
    """
    groups: Dict[str, List[TradeHistoryRow]] = {}
    for row in rows:
        groups.setdefault(row.scenario_name, []).append(row)
    return [
        TradeScenarioTotals(
            scenario_name=name,
            currency=group[0].currency,
            trade_count=len(group),
            gross_pnl=sum(r.gross_pnl for r in group),
            net_pnl=sum(r.net_pnl for r in group),
            total_fees=sum(r.total_fees for r in group),
        )
        for name, group in groups.items()
    ]


# --- Execution totals (currency-agnostic counts) --------------------------------------

def aggregate_execution_totals(rows: List[ExecutionStatsRow]) -> ExecutionStatsTotals:
    """Sum the per-unit order counts (currency-agnostic) into one totals object."""
    return ExecutionStatsTotals(
        orders_sent=sum(r.orders_sent for r in rows),
        orders_executed=sum(r.orders_executed for r in rows),
        orders_rejected=sum(r.orders_rejected for r in rows),
        sl_tp_triggered=sum(r.sl_tp_triggered for r in rows),
    )


# --- Portfolio roll-up (per account currency) -----------------------------------------

def aggregate_portfolio_by_currency(
    rows: List[PortfolioUnitRow]) -> List[PortfolioAggregateRow]:
    """
    Per-currency portfolio roll-up: group the unit rows by account currency, sum the
    additive headline figures, and recompute the ratios (win rate / profit factor) from the
    sums — never sum ratios. Drawdown is the worst (largest magnitude) across the group.
    Mirrors the console `PortfolioAggregator` formulas so report and console stay identical.

    Args:
        rows: The portfolio per-unit rows

    Returns:
        One PortfolioAggregateRow per currency present (sorted)
    """
    groups: Dict[str, List[PortfolioUnitRow]] = {}
    for row in rows:
        groups.setdefault(row.currency, []).append(row)
    return [_portfolio_aggregate(c, groups[c]) for c in sorted(groups)]


def _portfolio_aggregate(currency: str, rows: List[PortfolioUnitRow]) -> PortfolioAggregateRow:
    """Roll up one currency group into a headline aggregate row."""
    total_trades = sum(r.total_trades for r in rows)
    winning_trades = sum(r.winning_trades for r in rows)
    losing_trades = sum(r.losing_trades for r in rows)
    total_profit = sum(r.total_profit for r in rows)
    total_loss = sum(r.total_loss for r in rows)
    win_rate = winning_trades / total_trades if total_trades > 0 else 0.0
    profit_factor = total_profit / total_loss if total_loss > 0 else (
        0.0 if total_profit == 0 else float('inf'))
    max_drawdown = 0.0
    for r in rows:
        if abs(r.max_drawdown) > abs(max_drawdown):
            max_drawdown = r.max_drawdown
    return PortfolioAggregateRow(
        currency=currency,
        unit_count=len(rows),
        total_trades=total_trades,
        winning_trades=winning_trades,
        losing_trades=losing_trades,
        win_rate=win_rate,
        profit_factor=profit_factor,
        total_profit=total_profit,
        total_loss=total_loss,
        net_profit=total_profit - total_loss,
        max_drawdown=max_drawdown,
        total_fees=sum(r.total_fees for r in rows),
    )


def _mean(values: List[float]) -> float:
    """Mean, or 0.0 for an empty list."""
    return sum(values) / len(values) if values else 0.0
