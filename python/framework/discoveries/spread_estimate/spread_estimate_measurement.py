"""
FiniexTestingIDE - Spread Estimate Measurement

How wide was the spread in years whose ticks recorded no quote? This reads a symbol's tick
archive file by file — the columns it needs and nothing else — and estimates the spread per UTC
day from the trades' aggressor sides. Where a file did record a quote, the quote is measured
beside the estimate, which is how the estimator is checked before it is trusted for the years
that have none.

Analysis plane: it reads the archive and computes, it writes nothing.
"""

from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

from python.data_management.index.tick_index_manager import TickIndexManager
from python.framework.data_preparation.tick_parquet_reader import read_tick_parquet
from python.framework.discoveries.spread_estimate.spread_estimator import (
    collapse_bursts,
    effective_spread_bps,
    opposite_side_pairs,
    quoted_spread_bps,
    roll_spread_bps,
    utc_day_numbers,
)
from python.framework.types.data_deficiency_types import DataDeficiency
from python.framework.types.spread_estimate_types import (
    QuoteRegime,
    SpreadDayEstimate,
    SpreadDayValidation,
    SpreadMeasurement,
    SpreadMeasurementRow,
)

# The columns the measurement reads — `bid` and `ask` also give the traded price its fallback
_COLUMNS = ['time_msc', 'last', 'bid', 'ask', 'tick_flags']
# A day's Roll estimate needs enough price changes to mean anything
_MIN_TRADES_FOR_ROLL = 100
_EPOCH = date(1970, 1, 1)

_GroupKey = Tuple[int, str, QuoteRegime]


class SpreadEstimateMeasurement:
    """
    Measures a symbol's spread from its trades, file by file over the tick archive.

    Args:
        tick_index: The tick index to read the symbol's files from; a loaded one is used as given
    """

    def __init__(self, tick_index: Optional[TickIndexManager] = None):
        self._tick_index = tick_index

    def measure(self, broker_type: str, symbol: str) -> SpreadMeasurement:
        """
        Measure a symbol over every tick file the index lists for it.

        Args:
            broker_type: The broker
            symbol: The symbol

        Returns:
            The measurement
        """
        if self._tick_index is None:
            self._tick_index = TickIndexManager()
            self._tick_index.load_index()
        entries = self._tick_index.get_symbol_entries(broker_type, symbol)
        return self.measure_entries(broker_type, symbol, entries)

    def measure_entries(self, broker_type: str, symbol: str,
                        entries: List[Dict]) -> SpreadMeasurement:
        """
        Measure the given tick index entries, read in the order of their start times.

        Args:
            broker_type: The broker
            symbol: The symbol
            entries: Tick index entries of one symbol

        Returns:
            The measurement
        """
        accumulator = _Accumulator()
        carry: Optional[Tuple[int, float, bool]] = None
        for entry in sorted(entries, key=lambda item: item['start_time'] or ''):
            carry = accumulator.add_file(entry, carry)
        return accumulator.result(broker_type, symbol)


class _Accumulator:
    """Collects the per-file figures by year, origin class and quote regime, and by day."""

    def __init__(self):
        self._spreads: Dict[_GroupKey, List[np.ndarray]] = defaultdict(list)
        self._trades: Dict[_GroupKey, int] = defaultdict(int)
        self._files: Dict[_GroupKey, int] = defaultdict(int)
        self._roll: Dict[_GroupKey, List[float]] = defaultdict(list)
        self._quoted: Dict[_GroupKey, List[np.ndarray]] = defaultdict(list)
        self._effective: Dict[_GroupKey, List[np.ndarray]] = defaultdict(list)
        self._day_spreads: Dict[int, List[np.ndarray]] = defaultdict(list)
        self._day_quoted: Dict[int, List[np.ndarray]] = defaultdict(list)
        self._group_days: Dict[_GroupKey, set] = defaultdict(set)

    def add_file(self, entry: Dict,
                 carry: Optional[Tuple[int, float, bool]]) -> Optional[Tuple[int, float, bool]]:
        """
        Read one tick file and add its figures.

        Args:
            entry: The file's tick index entry
            carry: The previous file's last kept trade (time, price, side), so a pair across
                the boundary is not lost

        Returns:
            This file's last kept trade, for the next file
        """
        frame = read_tick_parquet(Path(entry['path']), columns=_COLUMNS)
        if frame.empty or 'tick_flags' not in frame.columns:
            return carry
        origin_class = str(entry.get('origin_class', 'unknown'))
        regime = (QuoteRegime.NO_QUOTE
                  if DataDeficiency.NO_QUOTE in entry.get('deficiencies', [])
                  else QuoteRegime.QUOTED)

        time_msc = frame['time_msc'].to_numpy(dtype=np.int64)
        price = frame['price'].to_numpy(dtype=np.float64)
        buy = (frame['tick_flags'] == 'BUY').to_numpy()
        trade_days = utc_day_numbers(time_msc)
        start_year = _year_of(int(trade_days[0]))
        self._files[(start_year, origin_class, regime)] += 1
        for day_year, count in _counts_by_year(trade_days).items():
            self._trades[(day_year, origin_class, regime)] += count

        keep = collapse_bursts(time_msc, buy)
        kept_time, kept_price, kept_buy = time_msc[keep], price[keep], buy[keep]
        if carry is not None:
            kept_time = np.concatenate(([carry[0]], kept_time))
            kept_price = np.concatenate(([carry[1]], kept_price))
            kept_buy = np.concatenate(([carry[2]], kept_buy))
        pair_time, spread = opposite_side_pairs(kept_time, kept_buy, kept_price)
        self._add_by_day(pair_time, spread, origin_class, regime, self._spreads, self._day_spreads)
        for day in np.unique(utc_day_numbers(pair_time)):
            self._group_days[(_year_of(int(day)), origin_class, regime)].add(int(day))

        for day, prices in _split_by_day(utc_day_numbers(kept_time), kept_price).items():
            if len(prices) >= _MIN_TRADES_FOR_ROLL:
                roll = roll_spread_bps(prices)
                if roll is not None:
                    self._roll[(_year_of(day), origin_class, regime)].append(roll)

        if regime is QuoteRegime.QUOTED:
            bid = frame['bid'].to_numpy(dtype=np.float64)
            ask = frame['ask'].to_numpy(dtype=np.float64)
            has_quote = ask > bid
            quoted = quoted_spread_bps(bid[has_quote], ask[has_quote])
            effective = effective_spread_bps(price[has_quote], bid[has_quote], ask[has_quote])
            quote_days = trade_days[has_quote]
            self._add_by_day(time_msc[has_quote], quoted, origin_class, regime, self._quoted,
                             self._day_quoted)
            for day_year, values in _split_by_year(quote_days, effective).items():
                self._effective[(day_year, origin_class, regime)].append(values)

        return int(kept_time[-1]), float(kept_price[-1]), bool(kept_buy[-1])

    def result(self, broker_type: str, symbol: str) -> SpreadMeasurement:
        """
        The collected figures as a measurement.

        Args:
            broker_type: The broker
            symbol: The symbol

        Returns:
            The measurement
        """
        keys = sorted(set(self._trades) | set(self._spreads),
                      key=lambda key: (key[0], key[1], key[2].value))
        rows = []
        for key in keys:
            spreads = _joined(self._spreads.get(key))
            quoted = _joined(self._quoted.get(key))
            effective = _joined(self._effective.get(key))
            rolls = self._roll.get(key, [])
            rows.append(SpreadMeasurementRow(
                year=key[0], origin_class=key[1], regime=key[2],
                files=self._files.get(key, 0),
                days=len(self._group_days.get(key, ())),
                trades=self._trades.get(key, 0),
                pairs=len(spreads),
                estimate_median_bps=_percentile(spreads, 50),
                estimate_p90_bps=_percentile(spreads, 90),
                estimate_mean_bps=_capped_mean(spreads),
                roll_median_bps=float(np.median(rolls)) if rolls else None,
                quoted_median_bps=_percentile(quoted, 50),
                quoted_p90_bps=_percentile(quoted, 90),
                effective_median_bps=_percentile(effective, 50),
                effective_p90_bps=_percentile(effective, 90),
                quoted_mean_bps=_capped_mean(quoted),
                effective_mean_bps=_capped_mean(effective),
            ))

        day_estimates = []
        for day in sorted(self._day_spreads):
            spreads = _joined(self._day_spreads[day])
            day_estimates.append(SpreadDayEstimate(
                day=_date_of(day), pairs=len(spreads),
                spread_median_bps=_percentile(spreads, 50),
                spread_p90_bps=_percentile(spreads, 90),
                spread_mean_bps=_capped_mean(spreads)))

        validation_days = []
        for day in sorted(self._day_quoted):
            spreads = _joined(self._day_spreads.get(day))
            quoted = _joined(self._day_quoted[day])
            validation_days.append(SpreadDayValidation(
                day=_date_of(day), pairs=len(spreads),
                estimate_median_bps=_percentile(spreads, 50),
                quoted_median_bps=_percentile(quoted, 50)))

        return SpreadMeasurement(broker_type=broker_type, symbol=symbol, rows=rows,
                                 day_estimates=day_estimates, validation_days=validation_days)

    def _add_by_day(self, time_msc: np.ndarray, values: np.ndarray, origin_class: str,
                    regime: QuoteRegime, by_group: Dict[_GroupKey, List[np.ndarray]],
                    by_day: Dict[int, List[np.ndarray]]) -> None:
        """
        File values under their year and under their day.

        Args:
            time_msc: The values' times in epoch milliseconds
            values: The values
            origin_class: The file's origin class
            regime: The file's quote regime
            by_group: Values by year, origin class and regime
            by_day: Values by day number
        """
        for day, day_values in _split_by_day(utc_day_numbers(time_msc), values).items():
            by_group[(_year_of(day), origin_class, regime)].append(day_values.astype(np.float32))
            by_day[day].append(day_values.astype(np.float32))


def _split_by_day(day_numbers: np.ndarray, values: np.ndarray) -> Dict[int, np.ndarray]:
    """
    Values grouped by their day number; the input is in time order.

    Args:
        day_numbers: Day number of each value
        values: The values

    Returns:
        Day number → that day's values
    """
    if len(values) == 0:
        return {}
    days, starts = np.unique(day_numbers, return_index=True)
    bounds = list(starts[1:]) + [len(values)]
    return {int(day): values[start:end] for day, start, end in zip(days, starts, bounds)}


def _split_by_year(day_numbers: np.ndarray, values: np.ndarray) -> Dict[int, np.ndarray]:
    """
    Values grouped by the year of their day.

    Args:
        day_numbers: Day number of each value
        values: The values

    Returns:
        Year → that year's values
    """
    by_year: Dict[int, List[np.ndarray]] = defaultdict(list)
    for day, day_values in _split_by_day(day_numbers, values).items():
        by_year[_year_of(day)].append(day_values.astype(np.float32))
    return {year: np.concatenate(parts) for year, parts in by_year.items()}


def _counts_by_year(day_numbers: np.ndarray) -> Dict[int, int]:
    """
    How many values fall in each year.

    Args:
        day_numbers: Day number of each value

    Returns:
        Year → count
    """
    counts: Dict[int, int] = defaultdict(int)
    days, day_counts = np.unique(day_numbers, return_counts=True)
    for day, count in zip(days, day_counts):
        counts[_year_of(int(day))] += int(count)
    return counts


def _joined(parts: Optional[List[np.ndarray]]) -> np.ndarray:
    """
    One array from collected parts.

    Args:
        parts: The parts, None or empty for none

    Returns:
        The joined array, empty without parts
    """
    if not parts:
        return np.empty(0, dtype=np.float32)
    return np.concatenate(parts)


def _percentile(values: np.ndarray, q: float) -> Optional[float]:
    """
    A percentile of values, None where there are none.

    Args:
        values: The values
        q: The percentile, 0–100

    Returns:
        The percentile
    """
    if len(values) == 0:
        return None
    return float(np.percentile(values, q))


def _capped_mean(values: np.ndarray) -> Optional[float]:
    """
    The mean of values up to their 99th percentile, None where there are none.

    The expected cost is a mean, not a median; the cap keeps the few pairs a price jump within a
    second puts far out from carrying it.

    Args:
        values: The values

    Returns:
        The capped mean
    """
    if len(values) == 0:
        return None
    cap = np.percentile(values, 99)
    return float(np.mean(values[values <= cap]))


def _date_of(day_number: int) -> date:
    """
    The calendar date of a day number.

    Args:
        day_number: Days since the epoch

    Returns:
        The date
    """
    return _EPOCH + timedelta(days=int(day_number))


def _year_of(day_number: int) -> int:
    """
    The year of a day number.

    Args:
        day_number: Days since the epoch

    Returns:
        The year
    """
    return _date_of(day_number).year
