"""
FiniexTestingIDE - Spread Estimate Types

A tick without a quote carries bid = ask = the traded price, so a backtest over it never crosses
a spread. The spread can be estimated from the trades themselves, because every trade records
its aggressor side: a buy that follows a sell within a moment crossed from the bid to the ask.
These types carry what that estimate measures, per day and summed over a year, beside the
quoted truth where the archive recorded a quote.
"""

from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import List, Optional, Tuple


class QuoteRegime(str, Enum):
    """Whether the files a figure was measured on recorded a quote."""

    NO_QUOTE = 'no_quote'
    QUOTED = 'quoted'


@dataclass
class SpreadDayEstimate:
    """
    The spread estimated over one UTC day of trades.

    Args:
        day: The UTC calendar day
        pairs: Opposite-side trade pairs the estimate rests on
        spread_median_bps: Median of the pairs' spreads, in basis points of the price; None
            without a pair
        spread_p90_bps: 90th percentile of the same; None without a pair
        spread_mean_bps: Mean of the pairs' spreads up to their 99th percentile — the expected
            cost; None without a pair
    """
    day: date
    pairs: int
    spread_median_bps: Optional[float]
    spread_p90_bps: Optional[float]
    spread_mean_bps: Optional[float] = None


@dataclass
class SpreadMeasurementRow:
    """
    One year of one origin class and quote regime: the estimate, the textbook reference and, where
    a quote was recorded, the truth.

    Args:
        year: The year the trades fall in
        origin_class: The origin class of the files read
        regime: Whether those files recorded a quote
        files: Files read
        days: Days with at least one pair
        trades: Trades read
        pairs: Opposite-side pairs the estimate rests on
        estimate_median_bps: Median of all pairs' spreads
        estimate_p90_bps: 90th percentile of all pairs' spreads
        estimate_mean_bps: Mean of the pairs' spreads up to their 99th percentile — the expected
            cost a model charges, robust to the few pairs a price jump puts far out
        roll_median_bps: Median of the daily Roll estimates, None where no day had one
        quoted_median_bps: Median quoted spread at the trades, None without a quote
        quoted_p90_bps: 90th percentile of the quoted spread, None without a quote
        effective_median_bps: Median effective spread, twice the distance of the trade price
            from the quote's midpoint; None without a quote
        effective_p90_bps: 90th percentile of the effective spread, None without a quote
        quoted_mean_bps: Mean quoted spread up to its 99th percentile, None without a quote
        effective_mean_bps: Mean effective spread up to its 99th percentile, None without a quote
    """
    year: int
    origin_class: str
    regime: QuoteRegime
    files: int
    days: int
    trades: int
    pairs: int
    estimate_median_bps: Optional[float]
    estimate_p90_bps: Optional[float]
    estimate_mean_bps: Optional[float]
    roll_median_bps: Optional[float]
    quoted_median_bps: Optional[float] = None
    quoted_p90_bps: Optional[float] = None
    effective_median_bps: Optional[float] = None
    effective_p90_bps: Optional[float] = None
    quoted_mean_bps: Optional[float] = None
    effective_mean_bps: Optional[float] = None


@dataclass
class SpreadDayValidation:
    """
    One quoted day: the estimate from the trades beside the quote the archive recorded.

    Args:
        day: The UTC calendar day
        pairs: Opposite-side pairs of the day
        estimate_median_bps: The estimator's median for the day
        quoted_median_bps: The median quoted spread at the day's trades
    """
    day: date
    pairs: int
    estimate_median_bps: Optional[float]
    quoted_median_bps: Optional[float]


@dataclass
class ModelledSpreadSummary:
    """
    What a scenario's ticks without a quote were given in place of one.

    Args:
        factor: The factor the estimated spread was multiplied by; 0 models nothing
        estimator_version: The estimator version the day estimates came from
        ticks_without_quote: The scenario's ticks from files that recorded no quote
        ticks_modelled: Of those, the ones that received a modelled quote — the rest had no
            usable estimate before their day and kept bid = ask
        median_spread_bps: The median full spread the modelled ticks received, in basis points
            of the price; None when none was modelled
        day_digest: SHA256 of the day estimates used and the factor, so two runs can be told
            apart by what their fills were charged
        quote_runs: The modelled stretches of the scenario's ticks, as (first, last) tick time in
            epoch milliseconds — what a fill's tick is looked up in
    """
    factor: float
    estimator_version: int
    ticks_without_quote: int
    ticks_modelled: int
    median_spread_bps: Optional[float]
    day_digest: str
    quote_runs: Tuple[Tuple[int, int], ...] = ()


@dataclass
class SpreadMeasurement:
    """
    A symbol's archive measured: per year, per day, and the quoted days beside their estimate.

    Args:
        broker_type: The broker the archive belongs to
        symbol: The symbol
        rows: One row per year, origin class and quote regime, in that order
        day_estimates: One estimate per UTC day with at least one pair, oldest first
        validation_days: One entry per day whose files recorded a quote, oldest first
    """
    broker_type: str
    symbol: str
    rows: List[SpreadMeasurementRow]
    day_estimates: List[SpreadDayEstimate]
    validation_days: List[SpreadDayValidation]
