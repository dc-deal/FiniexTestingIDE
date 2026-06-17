"""
Order-history report IO + extraction (#391) — twin of the trade-history IO.

Extract the shared `List[OrderResult]` from either pipeline's run result, persist
the built report as JSON + CSV in the run directory, read it back, and apply the
shared filter path. One model, one filter, identical data across console/file/API.
"""

import csv
from pathlib import Path
from typing import Optional

from python.framework.types.api.report_types import OrderHistoryReport, OrderHistoryRow

# Canonical artifact names inside a run directory
ORDER_HISTORY_ARTIFACT = 'order_history.json'
ORDER_HISTORY_CSV = 'order_history.csv'


def write_order_history_report(report: OrderHistoryReport, run_dir: Path) -> Path:
    """
    Persist the report as JSON in the run directory (the API's source).

    Args:
        report: The built order-history report
        run_dir: The run's directory

    Returns:
        Path of the written artifact
    """
    path = Path(run_dir) / ORDER_HISTORY_ARTIFACT
    path.write_text(report.model_dump_json(indent=2))
    return path


def read_order_history_report(path: Path) -> OrderHistoryReport:
    """Read a persisted order-history report artifact."""
    return OrderHistoryReport.model_validate_json(Path(path).read_text())


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
    columns = list(OrderHistoryRow.model_fields.keys())
    with path.open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in report.orders:
            writer.writerow(row.model_dump())
    return path


def filter_order_history_report(
    report: OrderHistoryReport,
    symbol: Optional[str] = None,
    status: Optional[str] = None,
) -> OrderHistoryReport:
    """
    Apply the shared row filter to an already-built report (the API path).

    Args:
        report: The full persisted report
        symbol: Keep only this symbol (None = all)
        status: Keep only this OrderStatus value (None = all)

    Returns:
        A new OrderHistoryReport with the filtered rows + recomputed metadata
    """
    rows = []
    for row in report.orders:
        if symbol is not None and row.symbol != symbol:
            continue
        if status is not None and row.status != status:
            continue
        rows.append(row)

    symbols = sorted({row.symbol for row in rows if row.symbol})
    return OrderHistoryReport(orders=rows, count=len(rows), symbols=symbols)
