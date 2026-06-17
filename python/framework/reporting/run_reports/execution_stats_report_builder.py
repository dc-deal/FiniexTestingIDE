"""
Execution-stats report builder (#391) — the order-count postprocessor.

Maps each run unit's ExecutionStats (currency-agnostic order counts + SL/TP triggers)
to the canonical ExecutionStatsReport: one row per unit + the summed totals (from the
shared aggregator). Pure + fixture-testable.
"""

from typing import List

from python.framework.reporting.run_reports.report_aggregators import aggregate_execution_totals
from python.framework.reporting.run_reports.run_unit import RunUnit
from python.framework.types.api.report_types import ExecutionStatsReport, ExecutionStatsRow
from python.framework.types.trading_env_types.trading_env_stats_types import ExecutionStats


def build_execution_stats_report(units: List[RunUnit]) -> ExecutionStatsReport:
    """
    Build the report from the run's units — one row per unit + summed totals.

    Args:
        units: The run's units (sim: scenarios; live: the session)

    Returns:
        ExecutionStatsReport with one row per unit (with stats) and the summed totals
    """
    rows = [
        _to_row(unit.name, unit.symbol, unit.execution_stats)
        for unit in units if unit.execution_stats is not None
    ]
    return ExecutionStatsReport(units=rows, totals=aggregate_execution_totals(rows))


def _to_row(name: str, symbol: str, stats: ExecutionStats) -> ExecutionStatsRow:
    """Map one unit's ExecutionStats to a renderable row."""
    return ExecutionStatsRow(
        name=name,
        symbol=symbol,
        orders_sent=stats.orders_sent,
        orders_executed=stats.orders_executed,
        orders_rejected=stats.orders_rejected,
        sl_tp_triggered=stats.sl_tp_triggered,
    )
