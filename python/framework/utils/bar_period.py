"""
FiniexTestingIDE - Bar Period (#588)

When a bar's period opens and closes, and whether a bar is complete at a given instant.

A bar is stamped with the instant its period OPENS — the archive (a resampled period is labelled
by its left edge), the renderer and the venues alike — but it is known only once its period has
ENDED: its high, low and close come from the whole period. Every question about which bars exist
at a moment therefore turns on the period's end, never on its opening stamp. Three places used
to answer it three ways: the backtest's warmup selection by the opening stamp, which took the bar
CONTAINING the start and so read hours of its future; the live fetch by the position of a row;
the renderer by the period's end. This module is the one answer, and every path asks it.

Every registry timeframe divides a UTC day, so the archive, the renderer and the venues share one
grid anchored at midnight UTC: a period is named by its opening instant, and the same instant
names the same period everywhere.

Pure functions — no state, no config manager, no tick.
"""

from datetime import datetime, timedelta

import pandas as pd

from python.framework.types.market_types.market_data_types import Bar
from python.framework.utils.timeframe_config_utils import TimeframeConfig


def period_start(instant: datetime, timeframe: str) -> datetime:
    """
    The opening instant of the period that contains an instant.

    Args:
        instant: A timezone-aware UTC instant
        timeframe: A registry timeframe (e.g. 'M5', 'D1')

    Returns:
        The opening instant of that period, on the timeframe's UTC grid
    """
    minutes = TimeframeConfig.get_minutes(timeframe)
    start_minute = ((instant.hour * 60 + instant.minute) // minutes) * minutes
    return instant.replace(
        hour=start_minute // 60, minute=start_minute % 60, second=0, microsecond=0)


def period_end(open_time: datetime, timeframe: str) -> datetime:
    """
    The instant at which a period ends — the moment its bar becomes known.

    Args:
        open_time: The period's opening instant (a bar's timestamp)
        timeframe: A registry timeframe

    Returns:
        The opening instant plus the timeframe's duration
    """
    return open_time + timedelta(minutes=TimeframeConfig.get_minutes(timeframe))


def is_complete_at(open_time: datetime, timeframe: str, instant: datetime) -> bool:
    """
    Whether the bar of a period is complete at an instant: its period has ended by then.

    Args:
        open_time: The period's opening instant (a bar's timestamp)
        timeframe: A registry timeframe
        instant: The moment asked about

    Returns:
        True when the period ends at or before the instant
    """
    return period_end(open_time, timeframe) <= instant


def complete_at_mask(open_times: pd.Series, timeframe: str, instant: datetime) -> pd.Series:
    """
    The same rule as is_complete_at, over a column of opening instants.

    Args:
        open_times: Timezone-aware UTC opening instants (a bar frame's timestamp column)
        timeframe: A registry timeframe
        instant: The moment asked about

    Returns:
        Boolean mask, True where the bar is complete at the instant
    """
    duration = pd.Timedelta(minutes=TimeframeConfig.get_minutes(timeframe))
    return open_times + duration <= instant


def open_time_of(bar: Bar) -> datetime:
    """
    A bar's opening instant as a datetime (a Bar carries it as an ISO string).

    Args:
        bar: Any bar

    Returns:
        The timezone-aware opening instant
    """
    return datetime.fromisoformat(bar.timestamp)


def last_complete_frame(
    bars: pd.DataFrame,
    timeframe: str,
    instant: datetime,
    count: int,
) -> pd.DataFrame:
    """
    The newest `count` bars of a bar frame that are complete at an instant — a warmup selection.

    Args:
        bars: Bars of one timeframe, oldest first, with a UTC 'timestamp' column of opening instants
        timeframe: Their timeframe
        instant: The instant the history is selected for
        count: How many bars the history holds at most

    Returns:
        The selected rows, oldest first
    """
    return bars[complete_at_mask(bars['timestamp'], timeframe, instant)].tail(count)
