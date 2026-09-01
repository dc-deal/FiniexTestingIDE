"""
Report row filters (#391) — the two artifacts the API filters server-side.

The API takes query parameters on trade and order history, so the filter has to run over the
persisted report rather than at build time. Real logic, hand-written, and shared: console, file
and API all filter through here, so a filtered view cannot disagree with itself between surfaces.

Kept apart from the generic artifact IO (#486), which owns only the shape every artifact repeats.
"""

from datetime import datetime
from typing import Optional

from python.framework.reporting.builders.report_aggregators import (
    aggregate_trade_analytics,
    aggregate_trade_scenario_totals,
)
from python.framework.types.api.report_types import OrderHistoryReport, TradeHistoryReport


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
        run_id=report.run_id,
        trades=rows, count=len(rows), symbols=symbols,
        analytics=aggregate_trade_analytics(rows),
        scenario_totals=aggregate_trade_scenario_totals(rows))


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
    return OrderHistoryReport(
        run_id=report.run_id, orders=rows, count=len(rows), symbols=symbols)
