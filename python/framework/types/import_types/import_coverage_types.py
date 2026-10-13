"""
Import Coverage Types.

What the archive already holds, as the import checks a new file against it.
"""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict


@dataclass
class ArchivedTickFile:
    """
    One tick parquet already in the archive, as the import sees it before writing another.

    Built from the tick index, so checking a new file against the archive opens no data file.

    Args:
        broker_type: The archive the file belongs to
        symbol: Trading symbol
        path: The parquet file
        source_file: The raw file it was imported from
        first_tick: Event time of its first tick (UTC)
        last_tick: Event time of its last tick (UTC)
        tick_count: Number of ticks
        file_size_mb: Size on disk
        metadata: The header fields a duplicate report compares
    """
    broker_type: str
    symbol: str
    path: Path
    source_file: str
    first_tick: datetime
    last_tick: datetime
    tick_count: int
    file_size_mb: float
    metadata: Dict[str, str] = field(default_factory=dict)
