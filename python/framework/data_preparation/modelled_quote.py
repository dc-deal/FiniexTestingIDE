"""
FiniexTestingIDE - Modelled Quote

A tick from a file without a quote carries bid = ask = the traded price, so a backtest over it
fills a buy at the price that traded rather than at the ask, and marks a long at it rather than
at the bid. The spread those days had is estimated from their trades (the spread estimate
discovery); this unit gives such a tick the quote that estimate implies — half the spread below
the traded price for the bid, half above it for the ask — before the ticks leave for the scenario
process. From there every rule that reads a quote reads it unchanged: a market order takes its
side, a resting order is reached by it, an open position is marked at it, in both account models.

A day is priced by the days BEFORE it, never by itself: the estimate of the last day with enough
pairs, at most FALLBACK_DAYS back. A tick with no such day keeps its recorded bid = ask and is
counted as not modelled. Measured 2026-10-16 over ETHUSD: a day's capped mean scatters no more
with 20-50 pairs than with over 100, so 20 pairs make a day usable.

Pure functions over arrays: no file, no cache, no state.
"""

import hashlib
import json
from datetime import date
from typing import List, Tuple

import numpy as np

from python.framework.discoveries.spread_estimate.spread_estimator import BPS, MS_PER_DAY
from python.framework.types.spread_estimate_types import SpreadDayEstimate

# A day priced from fewer pairs than this is not used to price the next one
MIN_PAIRS_PER_DAY = 20
# How far back the last usable day may lie before a tick is left unmodelled
FALLBACK_DAYS = 30
# The epoch as an ordinal, so a date turns into the day number tick times divide into
_EPOCH_ORDINAL = date(1970, 1, 1).toordinal()


def usable_days(estimates: List[SpreadDayEstimate]) -> Tuple[np.ndarray, np.ndarray]:
    """
    The days whose estimate may price a later day, and the half spread each implies.

    Args:
        estimates: Day estimates, oldest first

    Returns:
        (day numbers since the epoch, half spread as a fraction of the price), both sorted by
        day; empty arrays when no day qualifies
    """
    usable = [estimate for estimate in estimates
              if estimate.pairs >= MIN_PAIRS_PER_DAY and estimate.spread_mean_bps is not None]
    days = np.array([estimate.day.toordinal() - _EPOCH_ORDINAL for estimate in usable],
                    dtype=np.int64)
    half_spread = np.array([estimate.spread_mean_bps / 2.0 / BPS for estimate in usable],
                           dtype=np.float64)
    return days, half_spread


def modelled_quote(time_msc: np.ndarray, price: np.ndarray, bid: np.ndarray, ask: np.ndarray,
                   without_quote: np.ndarray, days: np.ndarray, half_spread: np.ndarray,
                   factor: float) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    The quote of each tick: modelled where its file recorded none and a usable day precedes it,
    recorded everywhere else.

    Args:
        time_msc: Tick times in epoch milliseconds, in tick order
        price: The traded price of each tick
        bid: The recorded bid of each tick
        ask: The recorded ask of each tick
        without_quote: True where the tick's file recorded no quote
        days: Usable day numbers, sorted (from `usable_days`)
        half_spread: Each usable day's half spread as a fraction of the price
        factor: The share of the estimate to apply; 0 models nothing

    Returns:
        (bid, ask, modelled mask, index into `days` of the day each tick was priced by — only
        meaningful where modelled)
    """
    count = len(time_msc)
    used = np.full(count, -1, dtype=np.int64)
    if count == 0 or factor <= 0.0 or len(days) == 0 or not without_quote.any():
        return bid, ask, np.zeros(count, dtype=bool), used

    tick_days = time_msc // MS_PER_DAY
    # The last usable day strictly before the tick's own day
    before = np.searchsorted(days, tick_days, side='left') - 1
    clipped = np.clip(before, 0, None)
    modelled = (without_quote & (before >= 0)
                & (tick_days - days[clipped] <= FALLBACK_DAYS))
    used = np.where(modelled, clipped, -1)

    traded = price.astype(np.float64)
    half = np.where(modelled, traded * half_spread[clipped] * factor, 0.0)
    modelled_bid = np.where(modelled, traded - half, bid)
    modelled_ask = np.where(modelled, traded + half, ask)
    return modelled_bid, modelled_ask, modelled, used


def modelled_runs(time_msc: np.ndarray, modelled: np.ndarray) -> Tuple[Tuple[int, int], ...]:
    """
    The stretches of consecutive modelled ticks, as (first, last) tick time.

    Args:
        time_msc: Tick times in epoch milliseconds, in tick order
        modelled: True where the tick's quote was modelled

    Returns:
        One (first, last) pair per stretch, in tick order; empty when nothing was modelled
    """
    if not modelled.any():
        return ()
    edges = np.diff(np.concatenate(([0], modelled.astype(np.int8), [0])))
    starts = np.flatnonzero(edges == 1)
    ends = np.flatnonzero(edges == -1) - 1
    return tuple((int(time_msc[start]), int(time_msc[end])) for start, end in zip(starts, ends))


def median_spread_bps(half_spread: np.ndarray, used: np.ndarray, modelled: np.ndarray,
                      factor: float) -> float:
    """
    The median full spread the modelled ticks were given, in basis points of the price.

    Args:
        half_spread: Each usable day's half spread as a fraction of the price
        used: Index into the usable days per tick, from `modelled_quote`
        modelled: The modelled mask
        factor: The factor applied

    Returns:
        The median; the caller asks only where something was modelled
    """
    return float(np.median(half_spread[used[modelled]]) * 2.0 * factor * BPS)


def day_digest(days: np.ndarray, half_spread: np.ndarray, used: np.ndarray,
               modelled: np.ndarray, factor: float, estimator_version: int) -> str:
    """
    The identity of what a scenario's fills were priced by: the days used, their estimates, the
    factor and the estimator version.

    Args:
        days: Usable day numbers
        half_spread: Their half spreads
        used: Index into the usable days per tick
        modelled: The modelled mask
        factor: The factor applied
        estimator_version: The estimator version of the day estimates

    Returns:
        A SHA256 hex digest
    """
    indexes = np.unique(used[modelled]) if modelled.any() else np.empty(0, dtype=np.int64)
    described = {
        'estimator_version': estimator_version,
        'factor': factor,
        'min_pairs_per_day': MIN_PAIRS_PER_DAY,
        'fallback_days': FALLBACK_DAYS,
        'days': [[int(days[i]), round(float(half_spread[i]), 12)] for i in indexes],
    }
    return hashlib.sha256(json.dumps(described, sort_keys=True).encode()).hexdigest()
