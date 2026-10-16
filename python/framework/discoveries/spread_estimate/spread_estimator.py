"""
Spread estimator - the bid-ask spread from trades whose aggressor side is known.

A venue that records the aggressor of every trade says which side of the book each trade took:
a buy at the ask, a sell at the bid. Two consecutive trades of opposite sides within a moment
therefore straddle the spread, and the price difference between them estimates it. Measured
2026-10-15 on Kraken files with a recorded quote: the median of these differences reproduced the
median quoted spread (ETHUSD 0.04 bps, BTCUSD 0.01 bps), while Roll's estimator, which reads
every price reversal as a bounce, came out 13-30 times too high.

A burst — several fills of one aggressive order, sharing one millisecond and one side — is
collapsed to its first fill: the first fill crosses the spread, the rest walk into the depth of
the book, and counting them would measure the depth rather than the spread.

Pure functions over arrays: no file, no index, no state.
"""

from typing import Optional, Tuple

import numpy as np

from python.framework.utils.trading_math.price_trigger import mid_price

# Two trades further apart than this are no longer read as straddling one quote
MAX_PAIR_GAP_MS = 1_000

BPS = 10_000.0
MS_PER_DAY = 86_400_000

# The version of the method: the pair rule and the burst collapse here, the capped mean the
# measurement takes over them. A cached day estimate is valid only under the version that wrote it.
ESTIMATOR_VERSION = 1


def collapse_bursts(time_msc: np.ndarray, buy: np.ndarray) -> np.ndarray:
    """
    Keep the first fill of each burst: consecutive trades in the same millisecond on the same side.

    Args:
        time_msc: Trade times in epoch milliseconds, in trade order
        buy: True where the aggressor bought

    Returns:
        Boolean mask of the trades kept
    """
    keep = np.ones(len(time_msc), dtype=bool)
    if len(time_msc) > 1:
        keep[1:] = (time_msc[1:] != time_msc[:-1]) | (buy[1:] != buy[:-1])
    return keep


def opposite_side_pairs(
    time_msc: np.ndarray,
    buy: np.ndarray,
    price: np.ndarray,
    max_gap_ms: int = MAX_PAIR_GAP_MS
) -> Tuple[np.ndarray, np.ndarray]:
    """
    The spread of every pair of consecutive trades on opposite sides close enough in time.

    Bursts are expected to be collapsed already (`collapse_bursts`).

    Args:
        time_msc: Trade times in epoch milliseconds, in trade order
        buy: True where the aggressor bought
        price: Trade prices
        max_gap_ms: The largest time between the two trades of a pair

    Returns:
        (the pairs' times in epoch milliseconds — the later trade's, the spreads in basis points
        of the pair's midpoint)
    """
    if len(time_msc) < 2:
        return np.empty(0, dtype=np.int64), np.empty(0, dtype=np.float64)
    is_pair = (buy[1:] != buy[:-1]) & ((time_msc[1:] - time_msc[:-1]) <= max_gap_ms)
    later = price[1:][is_pair]
    earlier = price[:-1][is_pair]
    spread_bps = np.abs(later - earlier) / mid_price(earlier, later) * BPS
    return time_msc[1:][is_pair], spread_bps


def roll_spread_bps(price: np.ndarray) -> Optional[float]:
    """
    Roll's estimator: the spread implied by the negative serial covariance of price changes.

    The textbook reference, reported beside the estimate and never used for it: it reads every
    reversal of the price as a bounce between bid and ask, which on a liquid venue with a spread
    of one price step it mostly is not.

    Args:
        price: Trade prices in trade order

    Returns:
        The estimate in basis points of the mean price, None where the covariance is not negative
        or there are too few prices
    """
    if len(price) < 3:
        return None
    changes = np.diff(price.astype(np.float64))
    later, earlier = changes[1:], changes[:-1]
    covariance = float(np.mean((later - later.mean()) * (earlier - earlier.mean())))
    mean_price = float(np.mean(price))
    # A covariance this close to zero is rounding, not a bounce — a steady trend lands here
    if covariance >= -1e-20 * mean_price ** 2:
        return None
    return 2.0 * np.sqrt(-covariance) / mean_price * BPS


def quoted_spread_bps(bid: np.ndarray, ask: np.ndarray) -> np.ndarray:
    """
    The quoted spread at each trade, in basis points of the quote's midpoint.

    Args:
        bid: The bid the trade was stamped with
        ask: The ask the trade was stamped with

    Returns:
        The spreads
    """
    return (ask - bid) / mid_price(bid, ask) * BPS


def effective_spread_bps(price: np.ndarray, bid: np.ndarray, ask: np.ndarray) -> np.ndarray:
    """
    The effective spread of each trade: twice its distance from the quote's midpoint.

    It is what a market order actually paid, and it exceeds the quoted spread where a fill went
    through the quote into the depth of the book.

    Args:
        price: Trade prices
        bid: The bid each trade was stamped with
        ask: The ask each trade was stamped with

    Returns:
        The effective spreads in basis points of the midpoint
    """
    midpoint = mid_price(bid, ask)
    return 2.0 * np.abs(price - midpoint) / midpoint * BPS


def utc_day_numbers(time_msc: np.ndarray) -> np.ndarray:
    """
    The UTC calendar day of each instant, as days since the epoch.

    Args:
        time_msc: Instants in epoch milliseconds

    Returns:
        Day numbers
    """
    return time_msc // MS_PER_DAY
