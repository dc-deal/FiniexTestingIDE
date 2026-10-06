"""
Gap File Attribution - Which File(s) a Gap Falls In

Answers the operator question behind every large gap: was the collector running
through it, or was it not collecting at all? The tick index carries everything
needed, so no data file is opened.
"""

import re
from bisect import bisect_left, bisect_right
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd

from python.framework.types.config_types.market_config_types import ServerClockConfig
from python.framework.types.coverage_report_types import Gap
from python.framework.utils.time_utils import server_clock_to_utc

# A file that rolls over while the collector runs opens at the instant the previous
# one closed. Measured over all 5216 archive transitions: 97.5% land within 60 s
# (p95 = 4 s), the rest is an order of magnitude higher. A few land marginally
# negative — the new file is opened while the old one still flushes.
ROLLOVER_TOLERANCE_S = 60

# Collector file names carry the collection start: <SYMBOL>_<YYYYMMDD>_<HHMMSS>
_OPEN_TIME_RE = re.compile(r'_(\d{8})_(\d{6})')


def parse_file_open_time(file_name: str,
                         server_clock: ServerClockConfig) -> Optional[pd.Timestamp]:
    """
    Read the collector's file-open time from a file name and convert it to UTC.

    The stamp is written in the collector's own clock — broker server time for
    MT5, UTC for Kraken — so it goes through the same server clock rule the importer
    applies to the tick times. The metadata's `start_time_unix` is NOT an alternative:
    it is the server wall-clock read as if it were UTC, so for MT5 it is off by the
    server's offset.

    Args:
        file_name: Parquet or JSON file name carrying the timestamp
        server_clock: The broker's server clock rule (market_config.json)

    Returns:
        The open time in UTC, or None if the name carries no timestamp or names a time
        inside an hour a daylight saving change repeats or skips
    """
    match = _OPEN_TIME_RE.search(file_name)
    if not match:
        return None

    opened = datetime.strptime(f'{match.group(1)}{match.group(2)}', '%Y%m%d%H%M%S')
    try:
        return pd.Timestamp(server_clock_to_utc(
            opened, server_clock.timezone, server_clock.hours_ahead))
    except ValueError:
        return None


def attribute_gaps_to_files(
    gaps: List[Gap],
    entries: List[Dict],
    server_clock: ServerClockConfig
) -> None:
    """
    Stamp each gap with the file holding the data before it, the file holding the
    data after it, and how long after that data ended the following file opened.

    Stamps in place — the gaps are already held by the report and a parallel
    result list would only invite an index mismatch. Gaps outside the indexed
    range keep their None fields rather than being guessed at.

    Args:
        gaps: Gaps to attribute (modified in place)
        entries: Tick index entries for one broker/symbol
        server_clock: The broker's server clock rule, for the open time in each file name
    """
    if not gaps or not entries:
        return

    bounds = sorted(
        (
            (pd.to_datetime(entry['start_time'], utc=True),
             pd.to_datetime(entry['end_time'], utc=True),
             entry['file'])
            for entry in entries
        ),
        key=lambda item: item[0]
    )
    starts = [start for start, _, _ in bounds]
    ends = [end for _, end, _ in bounds]

    for gap in gaps:
        if gap.gap_start is None or gap.gap_end is None:
            continue

        before = _last_file_at_or_before(bounds, starts, pd.Timestamp(gap.gap_start))
        after = _first_file_at_or_after(bounds, ends, pd.Timestamp(gap.gap_end))
        if before is None or after is None:
            continue

        gap.file_before = before[2]
        gap.file_after = after[2]

        if before is after:
            continue

        opened = parse_file_open_time(after[2], server_clock)
        if opened is None:
            continue

        # Measured from the last tick of the preceding file, not from the gap
        # start: a gap split at a market boundary carries synthetic segment
        # edges that lie in the hole and would distort the distance.
        gap.next_file_opened_after_s = (opened - before[1]).total_seconds()


def _last_file_at_or_before(
    bounds: List, starts: List, moment: pd.Timestamp
) -> Optional[tuple]:
    """
    The last file holding data at or before a moment.

    Located by start time rather than by exact match — bar timestamps are floored
    to the bar interval, so equality would never hit. When the moment falls in a
    hole between two files, this is the file preceding the hole, which is what
    "the data before the gap" means. Correct as long as files do not overlap,
    which the import's archive-ordering check enforces.

    Args:
        bounds: (start_time, end_time, file) triples sorted ascending
        starts: The start_time values, for the search
        moment: The instant to locate

    Returns:
        The bounds triple, or None when the moment precedes the first file
    """
    idx = bisect_right(starts, moment) - 1
    if idx < 0:
        return None
    return bounds[idx]


def _first_file_at_or_after(
    bounds: List, ends: List, moment: pd.Timestamp
) -> Optional[tuple]:
    """
    The first file holding data at or after a moment.

    Located by end time: a file whose data ends before the moment cannot carry
    it, so the first file still running at or past it is the one that resumes.

    Args:
        bounds: (start_time, end_time, file) triples sorted ascending
        ends: The end_time values, for the search
        moment: The instant to locate

    Returns:
        The bounds triple, or None when the moment follows the last file
    """
    idx = bisect_left(ends, moment)
    if idx >= len(bounds):
        return None
    return bounds[idx]
