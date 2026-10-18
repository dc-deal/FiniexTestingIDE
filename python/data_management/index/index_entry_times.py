"""
Index entry times - the start and end of the files an archive index lists.

The tick and signal indexes hold each file's first and last instant as ISO-8601 strings. Every
reader that compares them against a window converts them, and converting one string at a time
is what made those readers slow, so the conversion is said once, here.
"""

from typing import Dict, List, Tuple

import pandas as pd


def entry_time_bounds(entries: List[Dict]) -> Tuple[pd.DatetimeIndex, pd.DatetimeIndex]:
    """
    The start and end times of index entries as UTC timestamps, in two conversions.

    Two calls for the whole list rather than two per entry: a scalar pd.to_datetime costs about
    0.3 ms, and one symbol holds hundreds of files (measured 2026-10-15: 0.4 s per tick lookup
    on a symbol of 641 files, 0.18 s per signal lookup on one of 173). Each string is read as
    ISO-8601 on its own — without the format, pandas takes it from the first string and refuses
    a time with a fraction after one without.

    Args:
        entries: Index entries, each with a start_time and an end_time

    Returns:
        (starts, ends) in the entries' order, NaT where an entry has no time
    """
    starts = pd.to_datetime([entry['start_time'] for entry in entries], utc=True,
                            format='ISO8601')
    ends = pd.to_datetime([entry['end_time'] for entry in entries], utc=True, format='ISO8601')
    return starts, ends
