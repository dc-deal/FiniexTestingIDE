"""
Gap File Attribution - Which File(s) a Gap Falls In, and Whose Data They Are

Answers the operator question behind every large gap: whose silence is it — a venue's
published history, where nobody traded, or our own collection — and, in our own data, was
the collector running through it, or was it not collecting at all? The tick index carries
everything needed, so no data file is opened.
"""

import re
from bisect import bisect_left
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import pandas as pd

from python.data_management.index.index_entry_times import entry_time_bounds
from python.framework.types.config_types.data_origin_config_types import OriginClass
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
    server_clock: ServerClockConfig,
    bar_interval: timedelta
) -> None:
    """
    Stamp each gap with the file holding the data before it, the file holding the
    data after it, the origin class of each, and how long after that data ended the
    following file opened.

    Stamps in place — the gaps are already held by the report and a parallel
    result list would only invite an index mismatch. Gaps outside the indexed
    range keep their None fields rather than being guessed at.

    Args:
        gaps: Gaps to attribute (modified in place)
        entries: Tick index entries for one broker/symbol
        server_clock: The broker's server clock rule, for the open time in each file name
        bar_interval: The interval of the bars the gaps were detected on
    """
    if not gaps or not entries:
        return

    file_starts, file_ends = entry_time_bounds(entries)
    bounds = sorted(
        zip(file_starts, file_ends,
            [entry['file'] for entry in entries],
            [_origin_class_of(entry) for entry in entries]),
        key=lambda item: item[0]
    )
    starts = [bound[0] for bound in bounds]
    ends = [bound[1] for bound in bounds]

    for gap in gaps:
        if gap.gap_start is None or gap.gap_end is None:
            continue

        # A gap starts at the OPEN of the last bar before it, and that bar's ticks run on to
        # its end — a file whose first tick falls inside the bar holds the data before the gap
        before = _last_file_opened_before(
            bounds, starts, pd.Timestamp(gap.gap_start + bar_interval))
        after = _first_file_at_or_after(bounds, ends, pd.Timestamp(gap.gap_end))
        if before is None or after is None:
            continue

        gap.file_before = before[2]
        gap.file_after = after[2]
        gap.origin_class_before = before[3]
        gap.origin_class_after = after[3]

        if before is after:
            continue

        opened = parse_file_open_time(after[2], server_clock)
        if opened is None:
            continue

        # Measured from the last tick of the preceding file, not from the gap
        # start: a gap split at a market boundary carries synthetic segment
        # edges that lie in the hole and would distort the distance.
        gap.next_file_opened_after_s = (opened - before[1]).total_seconds()


def _origin_class_of(entry: Dict) -> OriginClass:
    """
    Read a tick index entry's origin class.

    The index writes 'unknown' for a file that carries no origin stamp, so an absent or
    unrecognised value reads as UNKNOWN — never as a class nobody declared.

    Args:
        entry: One tick index entry

    Returns:
        The file's origin class
    """
    try:
        return OriginClass(entry.get('origin_class', OriginClass.UNKNOWN.value))
    except ValueError:
        return OriginClass.UNKNOWN


def _last_file_opened_before(
    bounds: List, starts: List, moment: pd.Timestamp
) -> Optional[tuple]:
    """
    The last file opened before a moment — the one holding the data just before it.

    Located by start time rather than by exact match — bar timestamps are floored
    to the bar interval, so equality would never hit. When the moment falls in a
    hole between two files, this is the file preceding the hole, which is what
    "the data before the gap" means. Correct as long as files do not overlap,
    which the import's archive-ordering check enforces.

    Args:
        bounds: (start_time, end_time, file, origin_class) tuples sorted ascending
        starts: The start_time values, for the search
        moment: The instant to locate — the END of the last bar before a gap

    Returns:
        The bounds tuple, or None when no file opened before the moment
    """
    idx = bisect_left(starts, moment) - 1
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
        bounds: (start_time, end_time, file, origin_class) tuples sorted ascending
        ends: The end_time values, for the search
        moment: The instant to locate

    Returns:
        The bounds tuple, or None when the moment follows the last file
    """
    idx = bisect_left(ends, moment)
    if idx >= len(bounds):
        return None
    return bounds[idx]
