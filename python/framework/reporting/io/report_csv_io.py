"""
Report CSV surfaces (#391) — the three artifacts that also ship a flat table.

Most report artifacts are JSON only. Three carry a CSV twin because their content IS a table
someone exports and merges elsewhere. That is real logic rather than a repeated shape, so it
stays hand-written here while the JSON side collapses onto the generic artifact IO (#486).

`run_id` leads every row: a CSV is the format that gets exported and merged, and a row without
its run is a row nobody can trace back (#475).
"""

import csv
from pathlib import Path

from python.framework.types.api.report_types import (
    ExecutionStatsReport,
    ExecutionStatsRow,
    OrderHistoryReport,
    OrderHistoryRow,
    TradeHistoryReport,
    TradeHistoryRow,
)

# Canonical CSV names inside a run directory
EXECUTION_STATS_CSV = 'execution_stats.csv'
ORDER_HISTORY_CSV = 'order_history.csv'
TRADE_HISTORY_CSV = 'trade_history.csv'


def write_trade_history_csv(report: TradeHistoryReport, run_dir: Path) -> Path:
    """
    Persist the report as a CSV table — the same columns as the JSON / API model,
    so CSV, console, and API show one table.

    Args:
        report: The built trade-history report
        run_dir: The run's directory

    Returns:
        Path of the written CSV
    """
    # Flat aggregate table: the nested per-fill executions (#393) are JSON-only —
    # they do not flatten into a single CSV row (same rule as the portfolio model).
    nested = {'entry_executions', 'exit_executions'}
    path = Path(run_dir) / TRADE_HISTORY_CSV
    columns = ['run_id'] + [k for k in TradeHistoryRow.model_fields if k not in nested]
    with path.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in report.trades:
            writer.writerow({'run_id': report.run_id, **row.model_dump(exclude=nested)})
    return path


def write_order_history_csv(report: OrderHistoryReport, run_dir: Path) -> Path:
    """
    Persist the report as a CSV table — same columns as the JSON / API model.

    Args:
        report: The built order-history report
        run_dir: The run's directory

    Returns:
        Path of the written CSV
    """
    path = Path(run_dir) / ORDER_HISTORY_CSV
    columns = ['run_id'] + list(OrderHistoryRow.model_fields.keys())
    with path.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in report.orders:
            writer.writerow({'run_id': report.run_id, **row.model_dump()})
    return path


def write_execution_stats_csv(report: ExecutionStatsReport, run_dir: Path) -> Path:
    """
    Persist the per-unit rows as a CSV table (same columns as the JSON unit rows).

    Args:
        report: The built execution-stats report
        run_dir: The run's directory

    Returns:
        Path of the written CSV
    """
    path = Path(run_dir) / EXECUTION_STATS_CSV
    columns = ['run_id'] + list(ExecutionStatsRow.model_fields)
    with path.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in report.units:
            writer.writerow({'run_id': report.run_id, **row.model_dump()})
    return path
