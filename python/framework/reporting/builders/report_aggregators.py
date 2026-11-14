"""
Report aggregators (#391) — the measures over the per-unit report rows.

One home for every section's aggregation: a pure `aggregate_*(rows) → aggregate(s)` over the
already-mapped model rows (the unified per-unit projection). Each folds through its fold table in
`reporting/aggregation/tables/` (#557), which declares how every figure combines. Keeping the aggregators together
(facts → measures) makes the pattern consistent and is the seam a future run-wide `RunSummary`
composes from (#390 prework). Counts aggregate currency-agnostically; P&L-denominated figures
group per account currency; ratios (win rate / profit factor) are recomputed from the summed
components, never summed.
"""

from typing import AbstractSet, Any, Dict, List, Optional

from python.framework.reporting.aggregation.aggregation_fold import (
    fold_group,
    fold_keyed,
    fold_rows,
    served_part,
)
from python.framework.reporting.aggregation.tables.execution_figure_table import (
    AGGREGATED_EXECUTION_TABLE,
    EXECUTION_TOTALS_TABLE,
)
from python.framework.reporting.aggregation.tables.order_history_figure_table import (
    FAILED_ORDER_REASON_TABLE,
    FAILED_ORDERS_OF_UNIT_TABLE,
)
from python.framework.reporting.aggregation.tables.portfolio_figure_table import (
    AGGREGATED_PORTFOLIO_TABLE,
    PENDING_TOTALS_TABLE,
    PORTFOLIO_HEADLINE_TABLE,
    SPOT_BALANCES_TABLE,
)
from python.framework.reporting.aggregation.tables.profiling_figure_table import (
    PROFILING_OPERATION_TABLE,
    PROFILING_TABLE,
)
from python.framework.reporting.aggregation.tables.run_summary_figure_table import (
    SIGNAL_CHANNEL_TABLE,
)
from python.framework.reporting.aggregation.tables.trade_figure_table import (
    TRADE_ANALYTICS_TABLE,
    TRADE_SCENARIO_TOTALS_TABLE,
    TRADE_SCENARIO_TOTALS_WITHOUT_QUOTE_TABLE,
)
from python.framework.reporting.aggregation.tables.worker_figure_table import (
    DECISION_TOTALS_TABLE,
    WORKER_DECISION_BOTTLENECKS_TABLE,
    WORKER_TOTALS_TABLE,
)
from python.framework.types.api.report_types import (
    AggregatedPortfolioRow,
    DecisionTotals,
    ExecutionStatsRow,
    ExecutionStatsTotals,
    FailedOrderReason,
    FailedOrdersOfUnit,
    OrderHistoryRow,
    PendingOrdersUnitRow,
    PortfolioAggregateRow,
    PortfolioUnitRow,
    ProfilingAggregate,
    ProfilingOperationRow,
    ProfilingUnitRow,
    SignalReport,
    TradeAnalytics,
    TradeHistoryRow,
    TradeScenarioTotals,
    WorkerDecisionBottlenecks,
    WorkerDecisionUnitRow,
    WorkerStatRow,
)
from python.framework.types.trading_env_types.order_types import (
    FAILED_ORDER_STATUSES,
    FailedOrderOfReason,
)

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
    return [TradeAnalytics(**figures) for figures in fold_rows(rows, TRADE_ANALYTICS_TABLE)]


# --- Failed orders (the order history's listing) --------------------------------------

def aggregate_failed_orders(rows: List[OrderHistoryRow]) -> Dict[str, Any]:
    """
    The failed orders among the listed rows, counted per unit and per status and reason.

    Args:
        rows: The order history's rows (already filtered)

    Returns:
        The report's `failed_listed`, `failed_by_unit` (first-appearance order) and
        `failed_by_reason` (the most frequent first, first-appearance order among equals)
    """
    failed = [row for row in rows if row.status in FAILED_ORDER_STATUSES]
    by_reason = [FailedOrderReason(**figures) for figures in fold_rows(
        [FailedOrderOfReason(row.status, row.failure_label()) for row in failed],
        FAILED_ORDER_REASON_TABLE)]
    by_reason.sort(key=lambda reason: -reason.count)
    return {
        'failed_listed': fold_group(failed, FAILED_ORDERS_OF_UNIT_TABLE)['listed'],
        'failed_by_unit': [FailedOrdersOfUnit(**figures)
                           for figures in fold_rows(failed, FAILED_ORDERS_OF_UNIT_TABLE)],
        'failed_by_reason': by_reason,
    }


# --- Trade per-scenario totals (the per-scenario table footer) ------------------------

def aggregate_trade_scenario_totals(
        rows: List[TradeHistoryRow],
        units_without_quote: AbstractSet[str] = frozenset()) -> List[TradeScenarioTotals]:
    """
    Per-scenario trade-table totals (the footer line): group the rows by `scenario_name`
    and sum gross / net / fees / spread, so the console (and the API) read the footer off the
    model.

    Args:
        rows: The report's trade rows (already filtered)
        units_without_quote: Units that read a tick file without a quote — their spread
            total is None, because no fill of theirs crossed a recorded spread

    Returns:
        One TradeScenarioTotals per scenario present (first-appearance order)
    """
    groups = fold_rows(rows, TRADE_SCENARIO_TOTALS_TABLE)
    # A unit that read no quote folds with the table that declares its spread total unmeasured
    without_quote = [row for row in rows if row.scenario_name in units_without_quote]
    if without_quote:
        unquoted = {figures['scenario_name']: figures for figures in
                    fold_rows(without_quote, TRADE_SCENARIO_TOTALS_WITHOUT_QUOTE_TABLE)}
        groups = [unquoted.get(figures['scenario_name'], figures) for figures in groups]
    return [TradeScenarioTotals(**figures) for figures in groups]


# --- Execution totals (currency-agnostic counts) --------------------------------------

def aggregate_execution_totals(rows: List[ExecutionStatsRow]) -> ExecutionStatsTotals:
    """Sum the per-unit order counts (currency-agnostic) into one totals object."""
    # No unit folds every count to 0
    return ExecutionStatsTotals(**fold_group(rows, EXECUTION_TOTALS_TABLE))


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
    return [PortfolioAggregateRow(currency=key[0], **figures) for key, figures in
            fold_keyed(rows, PORTFOLIO_HEADLINE_TABLE)]


def aggregate_full_portfolio(
    rows: List[PortfolioUnitRow],
    exec_by_name: Dict[str, ExecutionStatsRow],
    pending_by_name: Dict[str, PendingOrdersUnitRow],
    currency: str,
    is_spot: bool,
    label: str,
) -> AggregatedPortfolioRow:
    """
    Roll up one currency (or margin/spot sub-) group into the rich aggregate (#397). Composes the
    lean headline (the headline table, byte-identical) and adds the cross-domain measures —
    balances, cost split, per-currency execution + pending (weighted-avg latency), and the spot
    dual-balance — each folded by its table in `reporting/aggregation/tables/`.

    Args:
        rows: The portfolio per-unit rows of this group
        exec_by_name / pending_by_name: execution / pending rows keyed by unit name
        currency / is_spot / label: group identity (label = '' | 'Margin' | 'Spot')

    Returns:
        AggregatedPortfolioRow
    """
    # The headline, and beside it the same units' rates, their execution counts, their pending
    # orders and, at spot, their dual balances — each folded by its table (#557)
    executions = [exec_by_name[r.name] for r in rows if r.name in exec_by_name]
    pendings = [pending_by_name[r.name] for r in rows if r.name in pending_by_name]
    spot = (served_part(fold_group(rows, SPOT_BALANCES_TABLE), SPOT_BALANCES_TABLE)
            if is_spot else {})
    return AggregatedPortfolioRow(
        headline=PortfolioAggregateRow(currency=currency,
                                       **fold_group(rows, PORTFOLIO_HEADLINE_TABLE)),
        is_spot=is_spot,
        label=label,
        **served_part(fold_group(rows, AGGREGATED_PORTFOLIO_TABLE), AGGREGATED_PORTFOLIO_TABLE),
        **served_part(fold_group(executions, AGGREGATED_EXECUTION_TABLE),
                      AGGREGATED_EXECUTION_TABLE),
        **served_part(fold_group(pendings, PENDING_TOTALS_TABLE), PENDING_TOTALS_TABLE),
        **spot,
    )


# --- Worker timing (summed across units, #398) ----------------------------------------

def aggregate_worker_totals(rows: List[WorkerDecisionUnitRow]) -> List[WorkerStatRow]:
    """
    Sum per-worker timing across all unit rows (keyed by worker name).

    avg is recomputed from summed total / summed call_count (never averaged); min/max
    are the extremes across units. Returned in descending total-time order.

    Args:
        rows: The per-unit worker/decision rows

    Returns:
        One WorkerStatRow per distinct worker, summed across the units
    """
    totals = [WorkerStatRow(**figures) for figures in
              fold_rows([w for row in rows for w in row.workers], WORKER_TOTALS_TABLE)]
    totals.sort(key=lambda w: w.total_time_ms, reverse=True)
    return totals


def aggregate_decision_totals(rows: List[WorkerDecisionUnitRow]) -> Optional[DecisionTotals]:
    """
    The decision logic's figures over the units that tracked their decisions.

    Args:
        rows: The tracked units' rows

    Returns:
        The totals; None where no unit tracked its decisions — nothing was counted, which zeros
        would claim was
    """
    if not rows:
        return None
    return DecisionTotals(**fold_group(rows, DECISION_TOTALS_TABLE))


def aggregate_worker_decision_bottlenecks(
        rows: List[WorkerDecisionUnitRow]) -> Optional[WorkerDecisionBottlenecks]:
    """
    The slowest performers over the units that tracked their decisions.

    Args:
        rows: The tracked units' rows

    Returns:
        The bottlenecks, each None where nothing qualifies; None where no unit tracked its
        decisions
    """
    if not rows:
        return None
    return WorkerDecisionBottlenecks(**fold_group(rows, WORKER_DECISION_BOTTLENECKS_TABLE))


# --- Profiling roll-up (cross-scenario, #399) -----------------------------------------

def aggregate_profiling(rows: List[ProfilingUnitRow], budget_active: bool) -> ProfilingAggregate:
    """
    Roll up the per-unit profiling rows into the run-level aggregate.

    Reproduces the console profiling summary's measures: cross-scenario avg/tick, the most
    common bottleneck, the P5 range, the P95-processing budget recommendation (P95 + 10%),
    the per-operation cross-scenario average call time, and the per-operation bottleneck
    frequency + status. Ratios are recomputed from summed components, never averaged-of-ratios.

    Args:
        rows: The per-unit profiling rows
        budget_active: Whether a tick-processing budget was configured (clipping simulated)

    Returns:
        The run-level ProfilingAggregate
    """
    if not rows:
        return ProfilingAggregate(budget_active=budget_active)
    operations = [ProfilingOperationRow(**figures) for figures in
                  fold_rows([op for row in rows for op in row.operations],
                            PROFILING_OPERATION_TABLE)]
    operations.sort(key=lambda o: o.avg_time_ms, reverse=True)
    return ProfilingAggregate(
        budget_active=budget_active,
        avg_operation_times=operations,
        **served_part(fold_group(rows, PROFILING_TABLE), PROFILING_TABLE))


# --- Signal resolution quality (run-wide, #433) ----------------------------------------

def aggregate_signal_fresh_ratio(report: SignalReport) -> Optional[float]:
    """
    The run's weakest SIGNAL channel: the MINIMUM fresh ratio over all usage rows.

    Minimum, not mean: a mean would hide a dead channel behind a healthy one, and the
    number exists to say how far the run's decisions can be trusted. Usages that
    processed no tick are skipped (nothing was decided on them).

    Args:
        report: The built signal report

    Returns:
        Ratio 0.0–1.0, or None when no SIGNAL worker resolved anything (deliberately not
        1.0 — that would claim a perfect feed where there was none)
    """
    usages = [usage for unit in report.units for usage in unit.usages]
    return fold_group(usages, SIGNAL_CHANNEL_TABLE)['signal_fresh_ratio']
