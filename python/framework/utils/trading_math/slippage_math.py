"""
FiniexTestingIDE - Slippage Math (#340)

How far a fill landed from the price it is measured against, signed so that a positive number is a
cost: a buy that paid more, a sell that received less. One formula for every report of slippage —
the trade history against the mid when the order was submitted, and the field study's certificate
against that mid, a limit or a trigger.
"""

from typing import Optional, Tuple

from python.framework.types.trading_env_types.order_types import OrderSide


def adverse_slippage(
    fill_price: float,
    reference_price: Optional[float],
    side: Optional[OrderSide],
) -> Tuple[Optional[float], Optional[float]]:
    """
    Adverse slippage of one fill against its reference: >0 = worse than the reference.

    Args:
        fill_price: What the fill executed at
        reference_price: What it is measured against — the mid at submission, a limit, a trigger
        side: The trading side; a BUY is worse above its reference, a SELL below it

    Returns:
        (price delta, percent of the reference), or (None, None) when the reference or the side
        was not captured
    """
    if reference_price is None or side is None:
        return None, None
    delta = ((fill_price - reference_price) if side is OrderSide.BUY
             else (reference_price - fill_price))
    pct = (delta / reference_price * 100.0) if reference_price else 0.0
    return delta, pct
