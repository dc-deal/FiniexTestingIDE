"""
Types of the one-time recovery of MT5 tick files a hard stop left unfinished.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class PartFileRecovery:
    """Where one unfinished file is cut, what it keeps and what it drops."""
    # The unfinished file as the collector left it (<name>_ticks.json.part)
    part_path: Path
    # Where the finished file is written (<name>_ticks.json, beside it)
    recovered_path: Path
    original_size_bytes: int
    # Length of the kept prefix: every byte up to and including the last complete tick
    cut_at_byte: int
    # Complete ticks in the kept prefix
    tick_count: int
    # collected_msc of the first and the last kept tick (UTC)
    first_collected_msc: Optional[int]
    last_collected_msc: Optional[int]
    # The last kept tick's own timestamp, in the file's server time — the form the collector
    # writes into summary.timing.end_time
    last_timestamp: Optional[str]
    # The bytes after the last complete tick: the tick the stop cut in half
    dropped_tail: str
    # Why the file cannot be recovered; None when it can
    refusal: Optional[str]
