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


@dataclass
class OwnProcessMemory:
    """
    What the calling process holds, read for itself (#611).

    `rss_bytes` counts every resident page, the ones it shares with other processes included;
    `private_bytes` only the pages this process alone holds — what it would give back on exit, and
    what a leak grows. Either is None where the system did not answer.
    """
    rss_bytes: Optional[int]
    private_bytes: Optional[int]
