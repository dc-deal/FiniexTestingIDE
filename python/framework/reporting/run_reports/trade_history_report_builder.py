"""
Trade-history report builder (#391) — the first postprocessor of the unified
reporting pipeline.

Pure function: a list of closed TradeRecords (the input both pipelines already
produce via `get_trade_history()`) → the canonical `TradeHistoryReport`. Runs off
the hot loop, source-agnostic, fixture-testable. Optional filters (symbol / close
reason / time range) live here so console, CSV, and API share one filter path.
"""

from datetime import datetime
from typing import List, Optional

from python.framework.types.api.report_types import TradeHistoryReport, TradeHistoryRow
from python.framework.types.portfolio_types.portfolio_trade_record_types import TradeRecord


def build_trade_history_report(
    trades: List[TradeRecord],
    symbol: Optional[str] = None,
    close_reason: Optional[str] = None,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> TradeHistoryReport:
    """
    Build the canonical trade-history report from closed trade records.

    Args:
        trades: Closed trade records (sim: aggregated across scenarios; live: the session)
        symbol: Keep only this symbol (None = all)
        close_reason: Keep only this CloseReason value, e.g. 'sl_triggered' (None = all)
        start: Keep trades whose entry_time >= start (None = no lower bound)
        end: Keep trades whose entry_time <= end (None = no upper bound)

    Returns:
        TradeHistoryReport with the filtered, mapped rows + distinct symbols
    """
    rows: List[TradeHistoryRow] = []
    for trade in trades:
        if symbol is not None and trade.symbol != symbol:
            continue
        if close_reason is not None and trade.close_reason.value != close_reason:
            continue
        if start is not None and trade.entry_time < start:
            continue
        if end is not None and trade.entry_time > end:
            continue
        rows.append(_to_row(trade))

    symbols = sorted({row.symbol for row in rows})
    return TradeHistoryReport(trades=rows, count=len(rows), symbols=symbols)


def _to_row(trade: TradeRecord) -> TradeHistoryRow:
    """Map one closed TradeRecord to a renderable row."""
    return TradeHistoryRow(
        position_id=trade.position_id,
        symbol=trade.symbol,
        direction=trade.direction.value,
        lots=trade.lots,
        entry_price=trade.entry_price,
        entry_time=trade.entry_time.isoformat(),
        exit_price=trade.exit_price,
        exit_time=trade.exit_time.isoformat(),
        duration_s=(trade.exit_time - trade.entry_time).total_seconds(),
        close_reason=trade.close_reason.value,
        gross_pnl=trade.gross_pnl,
        total_fees=trade.total_fees,
        net_pnl=trade.net_pnl,
    )
