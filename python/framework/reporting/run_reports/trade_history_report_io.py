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
from typing import List, Optional

from python.framework.types.api.report_types import TradeHistoryReport, TradeHistoryRow
from python.framework.types.autotrader_types.autotrader_result_types import AutoTraderResult
from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.types.portfolio_types.portfolio_trade_record_types import TradeRecord

# Canonical artifact names inside a run directory
TRADE_HISTORY_ARTIFACT = 'trade_history.json'
TRADE_HISTORY_CSV = 'trade_history.csv'


def trade_records_from_batch(batch: BatchExecutionSummary) -> List[TradeRecord]:
    """Aggregate the closed trade records across all scenarios of a sim batch."""
    records: List[TradeRecord] = []
    for result in batch.process_result_list:
        tick_loop = getattr(result, 'tick_loop_results', None)
        if tick_loop and tick_loop.trade_history:
            records.extend(tick_loop.trade_history)
    return records


def trade_records_from_session(session: AutoTraderResult) -> List[TradeRecord]:
    """The closed trade records of a live AutoTrader session (the single unit)."""
    return list(session.trade_history)


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
    path = Path(run_dir) / TRADE_HISTORY_CSV
    columns = list(TradeHistoryRow.model_fields.keys())
    with path.open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in report.trades:
            writer.writerow(row.model_dump())
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
    return TradeHistoryReport(trades=rows, count=len(rows), symbols=symbols)
