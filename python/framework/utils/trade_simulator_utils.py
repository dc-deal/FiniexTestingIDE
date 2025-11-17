"""
FiniexTestingIDE - Trade Simulator Utilities
Helper functions for trade simulator operations

Contains:
- pending_order_to_position(): Convert PendingOrder to Position for Decision Logic
"""

from python.framework.types.latency_simulator_types import PendingOrder
from python.framework.types.portfolio_types import Position, PositionStatus


def pending_order_to_position(pending_order: PendingOrder) -> Position:
    """
    Convert pending OPEN order to Position for Decision Logic.

    Creates a pseudo-Position from PendingOrder for unified exposure view.
    Position has pending=True flag and uses order_id as temporary position_id.

    Args:
        pending_order: PendingOrder with OPEN action

    Returns:
        Position object with pending=True flag

    Example:
        pending = PendingOrder(
            order_id="pending_ord_19",
            symbol="EURUSD",
            direction=OrderDirection.LONG,
            lots=0.1,
            entry_price=1.18000,
            entry_time=datetime.now(timezone.utc),
            ...
        )

        position = pending_order_to_position(pending)
        # position.position_id == "pending_ord_19" (uses order_id)
        # position.pending == True
    """
    return Position(
        position_id=pending_order.pending_order_id,
        symbol=pending_order.symbol,
        direction=pending_order.direction,
        lots=pending_order.lots,
        entry_price=pending_order.entry_price,
        entry_time=pending_order.entry_time,
        pending=True,  # Mark as pseudo-position
        stop_loss=pending_order.order_kwargs.get('stop_loss'),
        take_profit=pending_order.order_kwargs.get('take_profit'),
        comment=pending_order.order_kwargs.get('comment', ''),
        status=PositionStatus.OPEN,
        fees=[]  # No fees until filled
    )
