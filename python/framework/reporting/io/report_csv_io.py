"""
Report CSV surfaces (#391) — the three artifacts that also ship a flat table.

Most report artifacts are JSON only. Three carry a CSV twin because their content IS a table
someone exports and merges elsewhere. That is real logic rather than a repeated shape, so it
stays hand-written here while the JSON side collapses onto the generic artifact IO (#486).

`run_id` leads every row: a CSV is the format that gets exported and merged, and a row without
its run is a row nobody can trace back (#475).
"""

from pathlib import Path

from python.framework.store.entry_members.run_entry_writer import RunEntryWriter
from python.framework.types.api.report_types import (
    ExecutionStatsReport,
    ExecutionStatsRow,
    OrderHistoryReport,
    OrderHistoryRow,
    TradeHistoryReport,
    TradeHistoryRow,
)
from python.framework.types.store_member_types import RunEntryMemberId


def write_trade_history_csv(report: TradeHistoryReport, writer: RunEntryWriter) -> Path:
    """
    Persist the report as a CSV table — the same columns as the JSON / API model,
    so CSV, console, and API show one table.

    Args:
        report: The built trade-history report
        writer: The run's writer

    Returns:
        Path of the written CSV
    """
    # Flat aggregate table: the nested per-fill executions (#393) are JSON-only —
    # they do not flatten into a single CSV row (same rule as the portfolio model).
    nested = {'entry_executions', 'exit_executions'}
    columns = ['run_id'] + [k for k in TradeHistoryRow.model_fields if k not in nested]
    # JSON mode: an enum is written as its value (`partial`, not `CloseType.PARTIAL`)
    return writer.write_table(
        RunEntryMemberId.TRADE_HISTORY_CSV, columns,
        ({'run_id': report.run_id, **row.model_dump(mode='json', exclude=nested)}
         for row in report.trades))


def write_order_history_csv(report: OrderHistoryReport, writer: RunEntryWriter) -> Path:
    """
    Persist the report as a CSV table — same columns as the JSON / API model.

    Args:
        report: The built order-history report
        writer: The run's writer

    Returns:
        Path of the written CSV
    """
    columns = ['run_id'] + list(OrderHistoryRow.model_fields.keys())
    # JSON mode: an enum is written as its value and an absent field as an empty cell.
    return writer.write_table(
        RunEntryMemberId.ORDER_HISTORY_CSV, columns,
        ({'run_id': report.run_id, **row.model_dump(mode='json')} for row in report.orders))


def write_execution_stats_csv(report: ExecutionStatsReport, writer: RunEntryWriter) -> Path:
    """
    Persist the per-unit rows as a CSV table (same columns as the JSON unit rows).

    Args:
        report: The built execution-stats report
        writer: The run's writer

    Returns:
        Path of the written CSV
    """
    columns = ['run_id'] + list(ExecutionStatsRow.model_fields)
    return writer.write_table(
        RunEntryMemberId.EXECUTION_STATS_CSV, columns,
        ({'run_id': report.run_id, **row.model_dump()} for row in report.units))
