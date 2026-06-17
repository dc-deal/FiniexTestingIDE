"""
Trade-history report builder (#391/#389/#393) — the postprocessor of the unified
reporting pipeline.

Maps closed TradeRecords → the canonical `TradeHistoryReport` (the full projection:
audit columns + #389 analytics + #330 per-fill executions). Source variants tag each
row with its run unit (`scenario_name`) so the console can group per scenario and
render purely from the model. Runs off the hot loop, fixture-testable. The shared
filter (symbol / close reason / time range) lives here so every surface filters identically.
"""

from datetime import datetime
from typing import List, Optional

from python.framework.types.api.report_types import (
    ExecutionRow, TradeAnalytics, TradeHistoryReport, TradeHistoryRow)
from python.framework.types.autotrader_types.autotrader_result_types import AutoTraderResult
from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.types.portfolio_types.portfolio_trade_record_types import TradeRecord
from python.framework.types.trading_env_types.broker_trade_types import BrokerTrade
from python.framework.types.trading_env_types.order_types import OrderSide


def build_trade_history_report(
    trades: List[TradeRecord],
    symbol: Optional[str] = None,
    close_reason: Optional[str] = None,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> TradeHistoryReport:
    """
    Build the report from a flat trade list (no unit context — rows tagged '').

    Args:
        trades: Closed trade records
        symbol / close_reason / start / end: Optional filters

    Returns:
        The filtered, mapped TradeHistoryReport
    """
    return _assemble([_to_row(t) for t in trades], symbol, close_reason, start, end)


def build_trade_history_report_from_batch(
    batch: BatchExecutionSummary,
    symbol: Optional[str] = None,
    close_reason: Optional[str] = None,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> TradeHistoryReport:
    """
    Build the report from a sim batch — each row tagged with its scenario name.

    Args:
        batch: The completed batch summary
        symbol / close_reason / start / end: Optional filters

    Returns:
        The filtered TradeHistoryReport with per-scenario `scenario_name` tags
    """
    rows: List[TradeHistoryRow] = []
    for result in batch.process_result_list:
        tick_loop = getattr(result, 'tick_loop_results', None)
        if not tick_loop or not tick_loop.trade_history:
            continue
        for trade in tick_loop.trade_history:
            rows.append(_to_row(trade, result.scenario_name))
    return _assemble(rows, symbol, close_reason, start, end)


def build_trade_history_report_from_session(
    session: AutoTraderResult,
    name: str,
    symbol: Optional[str] = None,
    close_reason: Optional[str] = None,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> TradeHistoryReport:
    """
    Build the report from a live session — all rows tagged with the session unit name.

    Args:
        session: The collected session result
        name: Session unit label (profile / symbol)
        symbol / close_reason / start / end: Optional filters

    Returns:
        The filtered TradeHistoryReport
    """
    rows = [_to_row(trade, name) for trade in (session.trade_history or [])]
    return _assemble(rows, symbol, close_reason, start, end)


def _assemble(
    rows: List[TradeHistoryRow],
    symbol: Optional[str],
    close_reason: Optional[str],
    start: Optional[datetime],
    end: Optional[datetime],
) -> TradeHistoryReport:
    """Apply the shared row filter + analytics and assemble the report (the one filter path)."""
    filtered: List[TradeHistoryRow] = []
    for row in rows:
        if symbol is not None and row.symbol != symbol:
            continue
        if close_reason is not None and row.close_reason != close_reason:
            continue
        if start is not None and datetime.fromisoformat(row.entry_time) < start:
            continue
        if end is not None and datetime.fromisoformat(row.entry_time) > end:
            continue
        filtered.append(row)

    symbols = sorted({row.symbol for row in filtered})
    return TradeHistoryReport(
        trades=filtered, count=len(filtered), symbols=symbols,
        analytics=analytics_by_currency(filtered))


def analytics_by_currency(rows: List[TradeHistoryRow]) -> List[TradeAnalytics]:
    """
    Per-currency analytics (#393): group the rows by account currency and compute
    one TradeAnalytics each, so the P&L-denominated fields (MAE/MFE) never mix
    currencies. Returns one entry per currency (sorted), [] for no rows.

    Args:
        rows: The report's trade rows (already filtered)

    Returns:
        One TradeAnalytics per currency present
    """
    groups: dict = {}
    for row in rows:
        groups.setdefault(row.currency, []).append(row)
    return [compute_trade_analytics(groups[c]) for c in sorted(groups)]


def compute_trade_analytics(rows: List[TradeHistoryRow]) -> TradeAnalytics:
    """
    Aggregate the per-row analytics (#389) for ONE currency group: expectancy + R
    distribution over the R-defined subset, MAE/MFE summaries over winners/losers.

    Args:
        rows: Trade rows of a single currency group

    Returns:
        The aggregate TradeAnalytics for that group
    """
    r_rows = [r for r in rows if r.r_multiple is not None]
    winners = [r for r in rows if r.net_pnl > 0]
    losers = [r for r in rows if r.net_pnl < 0]
    return TradeAnalytics(
        currency=rows[0].currency if rows else '',
        trade_count=len(rows),
        expectancy=_mean([r.r_multiple for r in r_rows]),
        avg_win_r=_mean([r.r_multiple for r in r_rows if r.net_pnl > 0]),
        avg_loss_r=_mean([r.r_multiple for r in r_rows if r.net_pnl < 0]),
        r_trade_count=len(r_rows),
        avg_mae_winners=_mean([r.mae_pnl for r in winners]),
        avg_mae_losers=_mean([r.mae_pnl for r in losers]),
        avg_mfe_losers=_mean([r.mfe_pnl for r in losers]),
    )


def _to_row(trade: TradeRecord, scenario_name: str = '') -> TradeHistoryRow:
    """Map one closed TradeRecord to a renderable row (the full #393 projection)."""
    pip = _pip_size(trade.digits)
    mae_dist = abs(trade.entry_price - trade.mae_price) if trade.mae_price > 0 else 0.0
    mfe_dist = abs(trade.mfe_price - trade.entry_price) if trade.mfe_price > 0 else 0.0
    r_multiple = (trade.net_pnl / trade.initial_risk) if trade.initial_risk else None
    entry_slip, entry_slip_pct = _slippage(
        trade.entry_price, trade.entry_submission.tick_mid_price, trade.entry_side)
    exit_slip, exit_slip_pct = _slippage(
        trade.exit_price, trade.exit_submission.tick_mid_price, trade.exit_side)
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
        currency=trade.account_currency,
        mae_price=trade.mae_price,
        mfe_price=trade.mfe_price,
        mae_pnl=trade.mae_pnl,
        mfe_pnl=trade.mfe_pnl,
        mae_pips=mae_dist / pip,
        mfe_pips=mfe_dist / pip,
        r_multiple=r_multiple,
        scenario_name=scenario_name,
        entry_tick_index=trade.entry_tick_index,
        exit_tick_index=trade.exit_tick_index,
        entry_type=trade.entry_type.value if trade.entry_type else '',
        stop_loss=trade.stop_loss,
        take_profit=trade.take_profit,
        entry_side=trade.entry_side.value if trade.entry_side else '',
        exit_side=trade.exit_side.value if trade.exit_side else '',
        entry_executions=_execution_rows(trade.entry_trades),
        exit_executions=_execution_rows(trade.exit_trades),
        entry_slippage=entry_slip,
        exit_slippage=exit_slip,
        entry_slippage_pct=entry_slip_pct,
        exit_slippage_pct=exit_slip_pct,
    )


def _slippage(fill_price: float, submission_mid: Optional[float], side):
    """
    Adverse submission-vs-fill slippage (#340): >0 = paid worse than the submission
    mid. Direction-aware (BUY: fill−mid, SELL: mid−fill). Returns (price_delta, pct),
    or (None, None) when no submission tick / side was captured.
    """
    if submission_mid is None or side is None:
        return None, None
    delta = (fill_price - submission_mid) if side is OrderSide.BUY else (submission_mid - fill_price)
    pct = (delta / submission_mid * 100.0) if submission_mid else 0.0
    return delta, pct


def _execution_rows(broker_trades: Optional[List[BrokerTrade]]) -> List[ExecutionRow]:
    """Map a trade's per-fill BrokerTrades (#330) to renderable execution rows."""
    return [
        ExecutionRow(
            trade_id=bt.trade_id,
            side=bt.side.value if bt.side else '',
            volume=bt.volume,
            price=bt.price,
            fee=bt.fee,
            fee_currency=bt.fee_currency,
            liquidity='maker' if bt.is_maker else 'taker',
            timestamp=bt.timestamp.isoformat() if bt.timestamp else '',
        )
        for bt in (broker_trades or [])
    ]


def _mean(values: List[float]) -> float:
    """Mean, or 0.0 for an empty list."""
    return sum(values) / len(values) if values else 0.0


def _pip_size(digits: int) -> float:
    """
    Forex-convention pip = 10^-(digits-1) (5-digit FX → 0.0001, 3-digit JPY → 0.01).
    Approximation only — crypto has no pip concept; the exact per-symbol pip_size is #167.
    """
    return 10.0 ** -(digits - 1)
