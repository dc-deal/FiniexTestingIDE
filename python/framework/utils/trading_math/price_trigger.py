"""
FiniexTestingIDE - Price Trigger Predicates

Has the market reached the price an order is waiting for? One answer for both worlds.

The comparison lived privately in the simulation executor while the DRY-RUN simulator had no
notion of price at all — so a rehearsal filled a resting order because time passed, and a
backtest filled it because the market arrived. Two answers to one question is what §19 forbids,
and here it also breaks the framework's central claim: a rehearsal that disagrees with the
backtest predicts nothing.

Pure functions over primitives, deliberately: the SIM asks with a TickData it already holds, the
dry-run simulator asks from inside the adapter's parse layer where no executor state exists.
Whether a quote is available at all, and whether it belongs to this order's symbol, stays with
the caller — that is executor bookkeeping, not arithmetic.

This is a single-source module: never reimplement it, never bypass it.
"""

from typing import Optional

from python.framework.types.trading_env_types.order_types import OrderDirection


def is_limit_reached(
    direction: OrderDirection,
    limit_price: float,
    bid: float,
    ask: float,
) -> bool:
    """
    Has the market come to the limit price?

    A limit waits for a BETTER price than the market offers: a buy limit sits below the ask and
    fills when the ask falls to it, a sell limit sits above the bid and fills when the bid rises.

    Args:
        direction: LONG for a buy limit, SHORT for a sell limit
        limit_price: The order's limit price
        bid: Current bid
        ask: Current ask

    Returns:
        True when the order would fill
    """
    if direction == OrderDirection.LONG:
        return ask <= limit_price
    return bid >= limit_price


def is_stop_reached(
    direction: OrderDirection,
    stop_price: float,
    bid: float,
    ask: float,
) -> bool:
    """
    Has the market broken through the stop trigger?

    The inverse of a limit: a stop waits for a WORSE price, because it exists to get out or to
    follow a breakout. A buy stop sits above the ask and triggers when the ask rises to it, a
    sell stop sits below the bid and triggers when the bid falls.

    Args:
        direction: LONG for a buy stop, SHORT for a sell stop
        stop_price: The order's trigger price
        bid: Current bid
        ask: Current ask

    Returns:
        True when the order would trigger
    """
    if direction == OrderDirection.LONG:
        return ask >= stop_price
    return bid <= stop_price


def taken_price(
    direction: OrderDirection,
    bid: float,
    ask: float,
) -> Optional[float]:
    """
    The side of the book this direction actually trades at.

    A buy pays the ask and a sell receives the bid. Trivial, and written down here for the
    same reason as its siblings above: the answer already exists in several places, and the
    dry-run simulator was about to hold a fourth copy of it.

    Args:
        direction: LONG pays the ask, SHORT receives the bid
        bid: Current bid
        ask: Current ask

    Returns:
        The price that side trades at
    """
    return ask if direction == OrderDirection.LONG else bid
