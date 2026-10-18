from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional

from python.framework.types.config_types.data_origin_config_types import OriginClass
from python.framework.utils.time_utils import format_duration


class GapCategory(Enum):
    """Gap classification categories"""
    SEAMLESS = 'seamless'
    WEEKEND = 'weekend'
    HOLIDAY = 'holiday'
    SHORT = 'short'
    MODERATE = 'moderate'
    LARGE = 'large'

    def is_data_gap(self) -> bool:
        """
        Whether this category is a stretch of the open market with no tick — nobody traded, or
        nothing was recorded — as opposed to a market closure or no gap at all.

        Returns:
            True for short, moderate and large
        """
        return self in (GapCategory.SHORT, GapCategory.MODERATE, GapCategory.LARGE)

    def is_market_closure(self) -> bool:
        """
        Whether this category is a time the market was closed.

        Returns:
            True for weekend and holiday
        """
        return self in (GapCategory.WEEKEND, GapCategory.HOLIDAY)


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

    The file and origin fields are stamped afterwards on the render path only (the batch
    validation path must not pay for the tick-index load), so they stay None when the gap
    was never attributed.

    WHOSE silence a gap is comes first, from the origin class of the files around it: in a
    venue's published history (`external`) nobody of ours was collecting, and a gap there is
    an hour in which nobody traded at the venue — there is nothing to re-collect. Only in a
    file our collector wrote do the file fields say something about the collector:

    - Both file names equal → the gap sits inside one file, so the collector
      was demonstrably running through it.
    - Different names → the gap sits at a file boundary. That alone says
      nothing: files roll at max_ticks_per_file, so a boundary can fall inside
      a running session by coincidence (measured: 97 of 178 archive cases).
      `next_file_opened_after_s` separates the two — near zero is a rollover,
      a longer value is the moment collection actually resumed.

    The two origin classes differ only at a seam, where one origin's data ends and the
    other's begins — the venue's history and our first collected tick, for example.
    """
    gap_seconds: float
    category: GapCategory
    reason: str
    gap_start: Optional[datetime] = None
    gap_end: Optional[datetime] = None
    file_before: Optional[str] = None
    file_after: Optional[str] = None
    next_file_opened_after_s: Optional[float] = None
    origin_class_before: Optional[OriginClass] = None
    origin_class_after: Optional[OriginClass] = None

    @property
    def origin_class(self) -> Optional[OriginClass]:
        """The origin class of the file the gap lies in or follows — the gap's own origin."""
        return self.origin_class_before

    @property
    def is_origin_seam(self) -> bool:
        """True when the gap lies where one origin's data ends and another's begins."""
        return (self.origin_class_before is not None
                and self.origin_class_after is not None
                and self.origin_class_before != self.origin_class_after)

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


@dataclass
class GapYearOriginSummary:
    """
    The gaps of one year and one data origin, counted per category.

    Weekend and holiday closures are not counted: they are a market's expected silence, not
    missing data. A gap belongs to the year it STARTED in, and to the origin class of the
    file it lies in or follows; a gap no tick file could be found for carries no origin class.
    """
    year: int
    origin_class: Optional[OriginClass]
    short: int
    moderate: int
    large: int
    hours_without_data: float
