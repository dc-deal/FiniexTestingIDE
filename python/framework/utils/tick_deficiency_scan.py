"""
Tick deficiency scan — what one tick file lacks, measured from its rows.

Pure functions over a tick DataFrame, so the tick index measures every file it scans and a test
can hand a frame in directly. Nothing here reads a data format version: a version states what a
collector was configured to declare, not what the data holds — the reconstructed arrival times
#453 found run straight through the middle of one declared version.
"""

from typing import Optional

import numpy as np
import pandas as pd

from python.framework.types.data_deficiency_types import (
    DataDeficiency,
    TickDeficiencyMeasurement,
    TradeIdGaps,
)

# The reconstruction fingerprints need enough rows to mean anything; a short file says nothing
# either way and is never called reconstructed.
_MIN_MULTI_TICK_SECONDS = 10
_MIN_ROWS_FOR_LAG = 50
# The MT5 reconstruction spread every second's ticks evenly over 0–999 ms, so EVERY multi-tick
# second ends on .999; recorded data does so by chance only.
_SECOND_END_SHARE = 0.99
# The Kraken reconstruction copied the event time for ticker ticks (~53 % of rows, median lag
# 0 ms); recorded data shows a receive lag of about 15 ms.
_COPIED_EVENT_TIME_SHARE = 0.25


def measure_tick_deficiencies(df: pd.DataFrame) -> TickDeficiencyMeasurement:
    """
    Measure what one tick file lacks.

    Args:
        df: The file's ticks, as stored

    Returns:
        The deficiencies found, in declaration order, and the trade-id measurement
    """
    found = []
    if _has_no_quote(df):
        found.append(DataDeficiency.NO_QUOTE)
    if _has_no_arrival_time(df):
        found.append(DataDeficiency.NO_ARRIVAL_TIME)
    elif _arrival_time_reconstructed(df):
        found.append(DataDeficiency.ARRIVAL_TIME_RECONSTRUCTED)

    gaps = measure_trade_id_gaps(df)
    if gaps is not None and gaps.is_a_hole():
        found.append(DataDeficiency.MISSING_TRADES)

    return TickDeficiencyMeasurement(deficiencies=found, trade_id_gaps=gaps)


def measure_trade_id_gaps(df: pd.DataFrame) -> Optional[TradeIdGaps]:
    """
    Count the venue's trade ids a file does not hold.

    Measurable only where every trade carries its id (Kraken from collector format 1.7.0 on).
    Elsewhere the answer is None — "not measurable", never zero, although a comparison against
    the venue's own record shows the same loss there.

    Args:
        df: The file's ticks, as stored

    Returns:
        The gaps, or None where the file carries no trade ids
    """
    if 'trade_id' not in df.columns:
        return None
    with_id = df[df['trade_id'].notna()]
    if with_id.empty:
        return None

    ids = with_id['trade_id'].astype('int64').to_numpy()
    unique_ids, first_rows = np.unique(ids, return_index=True)
    span = int(unique_ids[-1] - unique_ids[0] + 1)
    steps = np.diff(unique_ids)
    holes = np.flatnonzero(steps > 1)
    if holes.size == 0:
        return TradeIdGaps(span=span, missing=0, runs=0, longest_run=0, longest_run_seconds=0.0)

    event_msc = _event_msc(with_id)[first_rows]
    hole_seconds = (event_msc[holes + 1] - event_msc[holes]) / 1000.0
    return TradeIdGaps(
        span=span,
        missing=int(span - unique_ids.size),
        runs=int(holes.size),
        longest_run=int((steps[holes] - 1).max()),
        longest_run_seconds=float(max(hole_seconds.max(), 0.0)),
    )


def _has_no_quote(df: pd.DataFrame) -> bool:
    """
    Whether no row carries a quote: bid equals ask throughout.

    Args:
        df: The file's ticks

    Returns:
        True when every row has bid == ask
    """
    if df.empty or 'bid' not in df.columns or 'ask' not in df.columns:
        return False
    return bool((df['bid'] == df['ask']).all())


def _has_no_arrival_time(df: pd.DataFrame) -> bool:
    """
    Whether the file carries no receive time — no column, or only the zero an older importer
    wrote for a file without one.

    Args:
        df: The file's ticks

    Returns:
        True when no arrival time is recorded
    """
    if 'collected_msc' not in df.columns:
        return True
    return not bool(df['collected_msc'].fillna(0).astype('int64').any())


def _arrival_time_reconstructed(df: pd.DataFrame) -> bool:
    """
    Whether the arrival times carry one of the two reconstruction fingerprints #453 measured.

    MT5: the reconstruction spread every event second's ticks evenly over 0–999 ms, so every
    second holding several ticks ends on .999. Kraken: it copied the event time for ticker
    ticks, so a large share of rows has no receive lag at all and the median lag is zero.

    Args:
        df: The file's ticks, arrival time present

    Returns:
        True when either fingerprint is present
    """
    if 'time_msc' not in df.columns:
        return False
    event = df['time_msc'].astype('int64')
    arrival = df['collected_msc'].fillna(0).astype('int64')

    per_second = arrival.groupby(event // 1000).agg(['size', 'last'])
    multi = per_second[per_second['size'] >= 2]
    if len(multi) >= _MIN_MULTI_TICK_SECONDS and (multi['last'] % 1000 == 999).mean() >= _SECOND_END_SHARE:
        return True

    if len(df) >= _MIN_ROWS_FOR_LAG:
        lag = arrival - event
        if (lag == 0).mean() >= _COPIED_EVENT_TIME_SHARE and float(lag.median()) == 0.0:
            return True
    return False


def _event_msc(df: pd.DataFrame) -> np.ndarray:
    """
    Event time per row in epoch milliseconds.

    Args:
        df: Ticks

    Returns:
        Milliseconds, from time_msc where present, else from the timestamp
    """
    if 'time_msc' in df.columns:
        return df['time_msc'].astype('int64').to_numpy()
    stamps = pd.to_datetime(df['timestamp'])
    return (stamps.astype('int64') // 1_000_000).to_numpy()
