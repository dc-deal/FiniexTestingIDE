"""
P&L conversion math — the single source for turning a price difference into a gross
P&L amount (account currency). Used by the live unrealized-P&L update and by derived
risk metrics (initial risk → R-multiple), so the conversion has exactly one home.
"""


def gross_pnl_from_price_diff(
    price_diff: float, digits: int, tick_value: float, lots: float) -> float:
    """
    Gross P&L (account currency) for a directional price difference.

    Args:
        price_diff: Signed price move in the position's favor (entry-relative)
        digits: Symbol price digits — points = price_diff * 10**digits
        tick_value: Account-currency value per point per lot
        lots: Position size

    Returns:
        Gross P&L = price_diff * 10**digits * tick_value * lots
    """
    points = price_diff * (10 ** digits)
    return points * tick_value * lots
