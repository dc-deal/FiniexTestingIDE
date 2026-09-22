"""
Booking-segment builder (#537) — one period's figures, from the records of that period.

This is the HAUPTBUCH step of the model the ledger is built on. The trade records are the
GRUNDBUCH — individual bookings, chronological. A segment is the period summary derived from
them. Everything above it (a deployment's total, a Sharpe ratio, a drawdown over a month) is
the ABSCHLUSS, derived in turn from the segments. Each level is believed because it can be
recomputed from the one below, and each carries a control total so it can disprove itself (§48).

**A trade belongs to the period it was CLOSED in.** Realised is booked, which is why the window
reads `exit_time` and why its end is exclusive: a trade realised exactly at a boundary falls in
the period that OPENS there, never in both. Identical to what LEAN does for its rolling windows
(`x.ExitTime.Date >= fromDate && x.ExitTime < toDate.AddDays(1)`), arrived at independently.

The consequence is worth stating because it is the one thing a reader gets wrong: a position
opened on Monday and closed on Tuesday puts its ENTIRE result on Tuesday, although it worked
overnight. That is correct bookkeeping and it is NOT the whole story about Monday — which is why
a segment carries its equity band beside its realised figures. The two differ by exactly the
unrealised movement across the boundary: flow is derived, stock is read (§48).

**Why this runs inside the tick loop and not off it.** §391 puts calculations in the builder,
away from the run, and the WRITE does move out — the coordinator writes every segment at once,
so no parquet write lands in what the benchmark measures. The arithmetic stays, deliberately:
`trade_history_max` bounds the record deque, so a long run drops its oldest records, and a
period derived after the fact would be a period whose records are gone. Derived at the seal, the
figures are captured while their records still exist.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import List

from python.framework.reporting.builders.report_aggregators import aggregate_trade_analytics
from python.framework.reporting.builders.trade_history_report_builder import (
    TradeWindowBasis,
    build_trade_history_report,
)
from python.framework.reporting.builders.run_unit import RunUnit
from python.framework.types.api.report_types import RunSummaryCurrency, TradeHistoryRow
from python.framework.types.portfolio_types.portfolio_trade_record_types import TradeRecord
from python.framework.types.run_results_types import BookingSegment, SegmentCloseReason


@dataclass
class SegmentSnapshot:
    """
    What is READ at the seal rather than derived from records — the stock half (§48).

    A balance, an equity, an open position count is true at an instant and cannot be summed out
    of a period's trades. The cumulative drawdown trio belongs here too: it runs across the whole
    deployment (#497) and is deliberately not a property of this period.

    Args:
        currency: The unit's account currency — one per unit, which is why a seal yields one
            segment rather than a list
        final_equity: The account value at the seal
        unrealized_pnl: What the open positions were worth against their entries at that instant
        open_position_count: How many were still open
        account_max_drawdown: The CUMULATIVE deepest decline, running across restarts (#497)
        max_equity: The peak it was measured against
        account_max_dd_pct: That decline as a share of the peak standing at the time
        segment_max_equity: The highest account value seen inside THIS period
        segment_min_equity: The lowest inside this period — tracked rather than derived, because
            the peak and the trough are different moments
        segment_max_drawdown: The deepest decline inside this period against its own peak
    """
    currency: str
    final_equity: float = 0.0
    unrealized_pnl: float = 0.0
    open_position_count: int = 0
    account_max_drawdown: float = 0.0
    max_equity: float = 0.0
    account_max_dd_pct: float = 0.0
    segment_max_equity: float = 0.0
    segment_min_equity: float = 0.0
    segment_max_drawdown: float = 0.0


def derive_booking_segment(
    unit_name: str,
    segment_no: int,
    opened_at: datetime,
    closed_at: datetime,
    reason: SegmentCloseReason,
    trades: List[TradeRecord],
    snapshot: SegmentSnapshot,
) -> BookingSegment:
    """
    Build one period's Hauptbuch entry from the records that fall inside it.

    The unit's WHOLE trade history is passed and windowed here rather than filtered by the
    caller: the window rule (exit time, end exclusive) is the definition of what a booking
    period IS, and a caller that filtered first would be a second place where that rule lives.

    Args:
        unit_name: The run unit — a scenario in the simulation, the session in live
        segment_no: Running number within that unit, starting at 1
        opened_at: When the period began (canonical clock)
        closed_at: When it ended (canonical clock)
        reason: What closed it
        trades: The unit's trade records; only those realised inside the window are used
        snapshot: The stock figures read at the seal

    Returns:
        The segment, with zero figures when nothing was realised in the window — a period in
        which the bot did not trade is a period, not an absence
    """
    rows = _window(unit_name, trades, opened_at, closed_at)
    return BookingSegment(
        segment_no=segment_no,
        unit_name=unit_name,
        opened_at=opened_at,
        closed_at=closed_at,
        reason=reason,
        trade_count=len(rows),
        figures=_figures(rows, snapshot),
        segment_max_equity=snapshot.segment_max_equity,
        segment_min_equity=snapshot.segment_min_equity,
        segment_max_drawdown=snapshot.segment_max_drawdown,
    )


def _window(
    unit_name: str,
    trades: List[TradeRecord],
    opened_at: datetime,
    closed_at: datetime,
) -> List[TradeHistoryRow]:
    """
    The trade rows realised inside the period.

    Routed through the shared report builder rather than filtered inline, so the mapping from
    record to row — MAE/MFE distances, R multiple, slippage, per-fill executions — exists once.

    Args:
        unit_name: Stamped onto the rows as their unit
        trades: The unit's full trade history
        opened_at / closed_at: The period, `[opened, closed)` on the EXIT basis

    Returns:
        The rows of this period
    """
    report = build_trade_history_report(
        run_id='',
        units=[RunUnit(name=unit_name, symbol='', trade_history=list(trades))],
        start=opened_at,
        end=closed_at,
        window_basis=TradeWindowBasis.EXIT,
    )
    return report.trades


def _figures(rows: List[TradeHistoryRow], snapshot: SegmentSnapshot) -> RunSummaryCurrency:
    """
    The period's KPIs — flow from the rows, stock from the snapshot.

    The two rates are recomputed from their own components rather than carried, which is what
    makes them recoverable at any level: a rate cannot be folded out of two periods, its
    components can be added on any of them.

    Args:
        rows: The period's trade rows
        snapshot: The stock figures read at the seal

    Returns:
        One currency's figures for this period
    """
    analytics = aggregate_trade_analytics(rows)
    stats = analytics[0] if analytics else None
    winners = [r for r in rows if r.net_pnl > 0]
    losers = [r for r in rows if r.net_pnl < 0]
    gross_profit = sum(r.net_pnl for r in winners)
    gross_loss = abs(sum(r.net_pnl for r in losers))
    total_trades = len(rows)

    # None rather than 0.0 where the quotient is undefined: a period that traded nothing has no
    # profit factor, and a period that traded and lost nothing has none either — a measured 0.0
    # would claim the bot made no profit, which is a different statement.
    if total_trades == 0:
        profit_factor = None
    elif gross_loss > 0:
        profit_factor = gross_profit / gross_loss
    else:
        profit_factor = 0.0 if gross_profit == 0 else None

    return RunSummaryCurrency(
        currency=snapshot.currency,
        net_pnl=gross_profit - gross_loss,
        profit_factor=profit_factor,
        win_rate=(len(winners) / total_trades) if total_trades else 0.0,
        total_fees=sum(r.total_fees for r in rows),
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        total_trades=total_trades,
        winning_trades=len(winners),
        losing_trades=len(losers),
        # --- stock: read at the seal, never summed out of the period's trades
        account_max_drawdown=snapshot.account_max_drawdown,
        max_equity=snapshot.max_equity,
        account_max_dd_pct=snapshot.account_max_dd_pct,
        unrealized_pnl=snapshot.unrealized_pnl,
        final_equity=snapshot.final_equity,
        open_position_count=snapshot.open_position_count,
        # --- risk-normalised and excursion figures, from the same single pass
        expectancy=stats.expectancy if stats else 0.0,
        avg_win_r=stats.avg_win_r if stats else None,
        avg_loss_r=stats.avg_loss_r if stats else None,
        r_trade_count=stats.r_trade_count if stats else 0,
        r_win_count=stats.r_win_count if stats else 0,
        r_loss_count=stats.r_loss_count if stats else 0,
        avg_mae_winners=stats.avg_mae_winners if stats else 0.0,
        avg_mae_losers=stats.avg_mae_losers if stats else 0.0,
        avg_mfe_losers=stats.avg_mfe_losers if stats else 0.0,
        largest_mae=stats.largest_mae if stats else 0.0,
        largest_mfe=stats.largest_mfe if stats else 0.0,
        avg_trade_duration_s=stats.avg_trade_duration_s if stats else 0.0,
        max_consecutive_wins=stats.max_consecutive_wins if stats else 0,
        max_consecutive_losses=stats.max_consecutive_losses if stats else 0,
    )


def describe_segment(segment: BookingSegment) -> str:
    """
    The one line a seal writes into the session log.

    It carries everything the ledger row will, because that is its job: the rows are written
    once, at the end, so a process that dies before that must leave its periods recoverable
    somewhere. The file log is that somewhere.

    Args:
        segment: The sealed period

    Returns:
        A single line, no trailing newline
    """
    f = segment.figures
    return (
        f'📕 Booking segment {segment.segment_no:03d} sealed — {segment.reason.value} · '
        f'{segment.opened_at.isoformat()} → {segment.closed_at.isoformat()} · '
        f'{segment.unit_name} · {segment.trade_count} trade(s) · '
        f'net {f.net_pnl:+.2f} {f.currency} · '
        f'gross +{f.gross_profit:.2f}/-{f.gross_loss:.2f} · '
        f'fees {f.total_fees:.2f} · '
        f'equity {f.final_equity:.2f} (band {segment.segment_min_equity:.2f}…'
        f'{segment.segment_max_equity:.2f}, DD {-abs(segment.segment_max_drawdown):.2f})'
    )
