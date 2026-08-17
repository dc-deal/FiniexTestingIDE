"""
Tick data format facts.

The one place that knows from which data_format_version on `collected_msc` is an authentic
collector timestamp. Older files carry a value synthesized by restore_collected_msc.py —
accurate for interval ordering, but not a record of real collection timing.
"""

from enum import Enum
from typing import Tuple

from python.framework.utils.version_utils import parse_version

# First data format version carrying authentic collected_msc.
MIN_AUTHENTIC_MSC_VERSION: Tuple[int, int, int] = (1, 3, 0)


class TickTimingOrigin(Enum):
    """Where a tick file's inter-tick timing comes from."""
    AUTHENTIC = 'authentic'   # collected_msc recorded by the collector
    RESTORED = 'restored'     # collected_msc synthesized by the restore
    UNKNOWN = 'unknown'       # no version recorded for the file


def classify_timing_origin(version: str) -> TickTimingOrigin:
    """
    Classify a data format version by the origin of its tick timing.

    Args:
        version: Data format version string (e.g. '1.3.0')

    Returns:
        TickTimingOrigin for that version
    """
    parsed = parse_version(version)
    if parsed is None:
        return TickTimingOrigin.UNKNOWN

    if parsed < MIN_AUTHENTIC_MSC_VERSION:
        return TickTimingOrigin.RESTORED

    return TickTimingOrigin.AUTHENTIC
