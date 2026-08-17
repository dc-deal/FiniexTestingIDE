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
    Gap between two files or within a file.

    For file-to-file gaps: file1 and file2 are populated
    For intra-file gaps: file1 and file2 are None
    """
    gap_seconds: float
    category: GapCategory
    reason: str
    gap_start: Optional[datetime] = None
    gap_end: Optional[datetime] = None

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
