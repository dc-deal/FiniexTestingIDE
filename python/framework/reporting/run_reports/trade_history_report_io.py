"""
Trade-history report IO + extraction (#391).

The bridge between the postprocessor and the consumers: extract the shared
`List[TradeRecord]` from either pipeline's run result, persist the built report as
JSON in the run directory, read it back, and apply the shared filter path. Console,
file, and API all go through this — one model, one filter, identical data.
"""

import csv
from datetime import datetime
from pathlib import Path
from typing import Optional

from python.framework.reporting.run_reports.report_aggregators import aggregate_trade_analytics
from python.framework.types.api.report_types import TradeHistoryReport, TradeHistoryRow

# Canonical artifact names inside a run directory
TRADE_HISTORY_ARTIFACT = 'trade_history.json'
TRADE_HISTORY_CSV = 'trade_history.csv'


def write_trade_history_report(report: TradeHistoryReport, run_dir: Path) -> Path:
    """
    Persist the report as JSON in the run directory (the API's source).

    Args:
        report: The built trade-history report
        run_dir: The run's directory (sim scenario-set run dir / autotrader run dir)

    Returns:
        Path of the written artifact
    """
    path = Path(run_dir) / TRADE_HISTORY_ARTIFACT
    path.write_text(report.model_dump_json(indent=2))
    return path


def read_trade_history_report(path: Path) -> TradeHistoryReport:
    """Read a persisted trade-history report artifact."""
    return TradeHistoryReport.model_validate_json(Path(path).read_text())


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
    columns = [k for k in TradeHistoryRow.model_fields if k not in nested]
    with path.open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in report.trades:
            writer.writerow(row.model_dump(exclude=nested))
    return path


def filter_trade_history_report(
    report: TradeHistoryReport,
    symbol: Optional[str] = None,
    close_reason: Optional[str] = None,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> TradeHistoryReport:
    """
    Apply the shared row filter to an already-built report (the API path).

    Args:
        report: The full persisted report
        symbol: Keep only this symbol (None = all)
        close_reason: Keep only this CloseReason value (None = all)
        start: Keep rows whose entry_time >= start (None = no lower bound)
        end: Keep rows whose entry_time <= end (None = no upper bound)

    Returns:
        A new TradeHistoryReport with the filtered rows + recomputed metadata
    """
    rows = []
    for row in report.trades:
        if symbol is not None and row.symbol != symbol:
            continue
        if close_reason is not None and row.close_reason != close_reason:
            continue
        if start is not None and datetime.fromisoformat(row.entry_time) < start:
            continue
        if end is not None and datetime.fromisoformat(row.entry_time) > end:
            continue
        rows.append(row)

    symbols = sorted({row.symbol for row in rows})
    return TradeHistoryReport(
        trades=rows, count=len(rows), symbols=symbols,
        analytics=aggregate_trade_analytics(rows))
