"""
FiniexTestingIDE - Block Boundary Report Builder
Extracts block boundary statistics from trade history and pending stats.

Used by process_tick_loop after close_all_remaining_orders() to capture
force-close impact for Profile Run disposition calculation (#214).
"""

from typing import List, Optional

from python.framework.types.portfolio_types.portfolio_trade_record_types import CloseReason, TradeRecord
from python.framework.types.process_data_types import BlockBoundaryReport
from python.framework.types.trading_env_types.pending_order_stats_types import PendingOrderStats


def build_block_boundary_report(
    trade_history: List[TradeRecord],
    pending_stats: Optional[PendingOrderStats]
) -> BlockBoundaryReport:
    """
    Build block boundary report from trade history and pending stats.

    Separates force-closed trades (CloseReason.SCENARIO_END) from
    naturally closed trades (SL/TP/MANUAL) and aggregates P&L per group.

    Args:
        trade_history: All completed trades from the tick loop
        pending_stats: Pending order statistics (may be None)

    Returns:
        BlockBoundaryReport with force-close vs. natural-close breakdown
    """
    if not trade_history:
        return BlockBoundaryReport(
            discarded_pending_orders=pending_stats.total_force_closed if pending_stats else 0
        )

    force_closed_trades = 0
    force_closed_pnl = 0.0
    natural_closed_trades = 0
    natural_closed_pnl = 0.0

    for trade in trade_history:
        if trade.close_reason == CloseReason.SCENARIO_END:
            force_closed_trades += 1
            force_closed_pnl += trade.net_pnl
        else:
            natural_closed_trades += 1
            natural_closed_pnl += trade.net_pnl

    return BlockBoundaryReport(
        force_closed_trades=force_closed_trades,
        force_closed_pnl=force_closed_pnl,
        natural_closed_trades=natural_closed_trades,
        natural_closed_pnl=natural_closed_pnl,
        discarded_pending_orders=pending_stats.total_force_closed if pending_stats else 0
    )
