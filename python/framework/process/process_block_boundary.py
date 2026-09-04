"""
FiniexTestingIDE - Block Boundary Report Builder
Extracts block boundary statistics from open positions, trade history and pending stats.

Used by process_tick_loop after finish_remaining_orders() to capture what the block edge
CUT, for Profile Run disposition calculation (#214).

What the edge does changed with #492 and the measure had to follow. The edge used to
force-close every open position, so its impact showed up as realised P&L on trades marked
`scenario_end`. Positions now stay open, so nothing carries that mark any more — reading it
would report every block as clean while the edge still cuts the same trades. The impact is
now the UNREALISED P&L on the positions the edge left open.
"""

from typing import List, Optional

from python.framework.types.portfolio_types.portfolio_types import Position
from python.framework.types.portfolio_types.portfolio_trade_record_types import TradeRecord
from python.framework.types.process_data_types import BlockBoundaryReport
from python.framework.types.trading_env_types.pending_order_stats_types import PendingOrderStats


def build_block_boundary_report(
    trade_history: List[TradeRecord],
    pending_stats: Optional[PendingOrderStats],
    open_positions: Optional[List[Position]] = None,
) -> BlockBoundaryReport:
    """
    Build block boundary report from open positions, trade history and pending stats.

    Args:
        trade_history: All completed trades from the tick loop
        pending_stats: Pending order statistics (may be None)
        open_positions: Positions still open when the block ended — the edge's impact

    Returns:
        BlockBoundaryReport with open-at-boundary vs. natural-close breakdown
    """
    positions = open_positions or []
    discarded = pending_stats.total_force_closed if pending_stats else 0

    if not trade_history and not positions:
        return BlockBoundaryReport(discarded_pending_orders=discarded)

    return BlockBoundaryReport(
        open_at_boundary_trades=len(positions),
        # Unrealised, and it is a running mark: a position that never saw a tick carries
        # 0.0, which is honest — nothing valued it — rather than an invented number.
        open_at_boundary_pnl=sum(position.unrealized_pnl for position in positions),
        natural_closed_trades=len(trade_history),
        natural_closed_pnl=sum(trade.net_pnl for trade in trade_history),
        discarded_pending_orders=discarded,
    )
