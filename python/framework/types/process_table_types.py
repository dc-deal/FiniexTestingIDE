"""
FiniexTestingIDE - Process Table Types
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class ProcessReading:
    """
    One process of the machine, as one pass over the process table saw it.

    `rss_bytes` is None where the process could not be read — gone between listing and reading,
    or not ours to read.
    """
    pid: int
    ppid: int
    rss_bytes: Optional[int]
