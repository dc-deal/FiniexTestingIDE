"""
Execution-stats report builder (#391) — the order-count postprocessor.

Maps the executor's ExecutionStats (currency-agnostic order counts + SL/TP triggers)
to the canonical ExecutionStatsReport: one row per run unit (sim: scenario; live:
session) + a single summed total. Counts are currency-agnostic, so the total is one
object (no per-currency split, unlike the portfolio roll-up). Pure + fixture-testable.
"""

from typing import List

from python.framework.types.api.report_types import (
    ExecutionStatsReport, ExecutionStatsRow, ExecutionStatsTotals)
from python.framework.types.autotrader_types.autotrader_result_types import AutoTraderResult
from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.types.trading_env_types.trading_env_stats_types import ExecutionStats


def build_execution_stats_report_from_batch(
    batch: BatchExecutionSummary) -> ExecutionStatsReport:
    """
    Build the report from a sim batch — one row per scenario + the summed total.

    Args:
        batch: The completed batch summary (scenarios = units)

    Returns:
        ExecutionStatsReport with one unit per scenario and the summed totals
    """
    rows: List[ExecutionStatsRow] = []
    for result in batch.process_result_list:
        tick_loop = getattr(result, 'tick_loop_results', None)
        if not tick_loop or tick_loop.execution_stats is None:
            continue
        scenario = batch.get_scenario_by_process_result(result)
        rows.append(_to_row(
            result.scenario_name, scenario.symbol, tick_loop.execution_stats))
    return ExecutionStatsReport(units=rows, totals=_sum_totals(rows))


def build_execution_stats_report_from_session(
    session: AutoTraderResult, name: str, symbol: str) -> ExecutionStatsReport:
    """
    Build the report from a live session (one unit = the total).

    Args:
        session: The collected session result
        name: Unit label (profile name / symbol)
        symbol: Traded symbol

    Returns:
        ExecutionStatsReport with the single session unit + its totals (empty if no stats)
    """
    if session.execution_stats is None:
        return ExecutionStatsReport(units=[], totals=ExecutionStatsTotals())
    rows = [_to_row(name, symbol, session.execution_stats)]
    return ExecutionStatsReport(units=rows, totals=_sum_totals(rows))


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


def _sum_totals(rows: List[ExecutionStatsRow]) -> ExecutionStatsTotals:
    """Sum the per-unit counts (currency-agnostic) into one totals object."""
    return ExecutionStatsTotals(
        orders_sent=sum(r.orders_sent for r in rows),
        orders_executed=sum(r.orders_executed for r in rows),
        orders_rejected=sum(r.orders_rejected for r in rows),
        sl_tp_triggered=sum(r.sl_tp_triggered for r in rows),
    )
