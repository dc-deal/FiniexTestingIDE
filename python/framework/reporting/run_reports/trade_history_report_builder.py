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

from python.framework.types.api.report_types import (
    TradeAnalytics, TradeHistoryReport, TradeHistoryRow)
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
    return TradeHistoryReport(
        trades=rows, count=len(rows), symbols=symbols,
        analytics=compute_trade_analytics(rows))


def compute_trade_analytics(rows: List[TradeHistoryRow]) -> TradeAnalytics:
    """
    Aggregate the per-row analytics (#389): expectancy + R distribution over the
    R-defined subset, MAE/MFE summaries over winners/losers. Recomputed per (filtered)
    report so the numbers always match the rows shown.

    Args:
        rows: The report's trade rows (already filtered)

    Returns:
        The aggregate TradeAnalytics
    """
    r_rows = [r for r in rows if r.r_multiple is not None]
    winners = [r for r in rows if r.net_pnl > 0]
    losers = [r for r in rows if r.net_pnl < 0]
    return TradeAnalytics(
        expectancy=_mean([r.r_multiple for r in r_rows]),
        avg_win_r=_mean([r.r_multiple for r in r_rows if r.net_pnl > 0]),
        avg_loss_r=_mean([r.r_multiple for r in r_rows if r.net_pnl < 0]),
        r_trade_count=len(r_rows),
        avg_mae_winners=_mean([r.mae_pnl for r in winners]),
        avg_mae_losers=_mean([r.mae_pnl for r in losers]),
        avg_mfe_losers=_mean([r.mfe_pnl for r in losers]),
    )


def _to_row(trade: TradeRecord) -> TradeHistoryRow:
    """Map one closed TradeRecord to a renderable row."""
    pip = _pip_size(trade.digits)
    mae_dist = abs(trade.entry_price - trade.mae_price) if trade.mae_price > 0 else 0.0
    mfe_dist = abs(trade.mfe_price - trade.entry_price) if trade.mfe_price > 0 else 0.0
    r_multiple = (trade.net_pnl / trade.initial_risk) if trade.initial_risk else None
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
        mae_price=trade.mae_price,
        mfe_price=trade.mfe_price,
        mae_pnl=trade.mae_pnl,
        mfe_pnl=trade.mfe_pnl,
        mae_pips=mae_dist / pip,
        mfe_pips=mfe_dist / pip,
        r_multiple=r_multiple,
    )


def _mean(values: List[float]) -> float:
    """Mean, or 0.0 for an empty list."""
    return sum(values) / len(values) if values else 0.0


def _pip_size(digits: int) -> float:
    """
    Forex-convention pip = 10^-(digits-1) (5-digit FX → 0.0001, 3-digit JPY → 0.01).
    Approximation only — crypto has no pip concept; the exact per-symbol pip_size is #167.
    """
    return 10.0 ** -(digits - 1)
