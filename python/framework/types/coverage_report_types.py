from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional

from python.framework.utils.time_utils import format_duration


class GapCategory(Enum):
    """Gap classification categories"""
    SEAMLESS = "seamless"
    WEEKEND = "weekend"
    HOLIDAY = "holiday"
    SHORT = "short"
    MODERATE = "moderate"
    LARGE = "large"


@dataclass
class IndexEntry:
    """
    Represents a single Parquet file in the index.
    """
    file: str
    path: str
    symbol: str
    start_time: datetime
    end_time: datetime
    tick_count: int
    file_size_mb: float
    source_file: str
    num_row_groups: int


@dataclass
class DataFormatVersionSpan:
    """
    One contiguous run of tick files sharing the same data_format_version.

    Spans are built from the chronologically sorted tick index, so their boundaries are
    the first and last tick time of the run. The version is the schema the collector was
    configured to declare — it carries no claim about the tick timing itself.
    """
    version: str
    start_time: datetime
    end_time: datetime
    file_count: int
    tick_count: int


@dataclass
class Gap:
    """
    A stretch of time without data, detected from the bar sequence.

    The file fields are stamped afterwards on the render path only (the batch
    validation path must not pay for the tick-index load), so they stay None
    when the gap was never attributed:

    - Both file names equal → the gap sits inside one file, so the collector
      was demonstrably running through it.
    - Different names → the gap sits at a file boundary. That alone says
      nothing: files roll at max_ticks_per_file, so a boundary can fall inside
      a running session by coincidence (measured: 97 of 178 archive cases).
      `next_file_opened_after_s` separates the two — near zero is a rollover,
      a longer value is the moment collection actually resumed.
    """
    gap_seconds: float
    category: GapCategory
    reason: str
    gap_start: Optional[datetime] = None
    gap_end: Optional[datetime] = None
    file_before: Optional[str] = None
    file_after: Optional[str] = None
    next_file_opened_after_s: Optional[float] = None

    @property
    def gap_hours(self) -> float:
        """Gap duration in hours"""
        return self.gap_seconds / 3600

    @property
    def duration_human(self) -> str:
        """Human-readable duration"""
        return format_duration(self.gap_seconds)

    @property
    def severity_icon(self) -> str:
        """Icon based on severity"""
        return {
            GapCategory.SEAMLESS: '✅',
            GapCategory.WEEKEND: '✅',
            GapCategory.HOLIDAY: '✅',  # Expected market closure
            GapCategory.SHORT: '⚠️ ',
            GapCategory.MODERATE: '⚠️ ',
            GapCategory.LARGE: '🔴'
        }.get(self.category, '❓')
