"""
FiniexTestingIDE - Awaited Order Types

What a session's stop waits for (#630): an order that went out and whose outcome this session does
not know yet. Whatever shows a stop reads the same row, so each surface names an order in the same
words.
"""

from dataclasses import dataclass
from typing import Optional

from python.framework.types.trading_env_types.order_types import (
    OrderAction,
    OrderDirection,
    OrderType,
)


@dataclass(frozen=True)
class AwaitedOrder:
    """
    One order on its way.

    Args:
        order_id: The order's own id; a market close carries its position's
        action: Whether it opens a position or closes one
        order_type: What it was submitted as
        direction: The direction of the position it opens or closes; None for a close whose
            position is gone
        lots: Its size; None for a whole close whose position is gone
        symbol: Its instrument; None for a close whose position is gone
        sent_at_monotonic_s: `time.monotonic()` when it was sent; None for an order a previous
            session sent, which this one adopted
        waits_for: What it waits for, in words a person reads
    """
    order_id: str
    action: OrderAction
    order_type: OrderType
    direction: Optional[OrderDirection]
    lots: Optional[float]
    symbol: Optional[str]
    sent_at_monotonic_s: Optional[float]
    waits_for: str

    def describe(self) -> str:
        """
        What the order is, as the operator reads it.

        Returns:
            For instance `market BUY 0.01 BTCUSD`, or `market close of LONG 0.01 BTCUSD`
        """
        kind = self.order_type.value
        size = f'{self.lots:g}' if self.lots is not None else 'whole size'
        symbol = self.symbol or 'unknown symbol'
        if self.action is OrderAction.CLOSE:
            held = self.direction.value.upper() if self.direction is not None else 'position'
            return f'{kind} close of {held} {size} {symbol}'
        side = 'BUY' if self.direction is OrderDirection.LONG else 'SELL'
        return f'{kind} {side} {size} {symbol}'
