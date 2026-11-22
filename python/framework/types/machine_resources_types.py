"""
FiniexTestingIDE - Machine Resources Types (#595)

What the machine this server runs on has and uses, read at one instant, and how close its memory
is to running out — and how much room the filesystems the stores live on still have (#632).
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import List, Optional

from python.framework.types.store_types import StoreId


class LimitSource(StrEnum):
    """Where a limit was read: the container's own cgroup, or the machine as a whole."""
    CGROUP = 'cgroup'
    MACHINE = 'machine'


class PressureLevel(StrEnum):
    """How close the machine's memory is to its limit, graded against configured thresholds."""
    OK = 'ok'
    WARN = 'warn'
    CRITICAL = 'critical'


@dataclass
class CpuReading:
    """
    The processors the server's processes may use, and how busy the machine was.

    Args:
        count: How many processors bind — a cgroup quota may be fractional
        count_source: Whether the count came from a cgroup quota or the machine
        used_pct: How busy the machine's processors were over the window; None when no window
            has passed yet
        window_s: The seconds that busy share was measured over; None with it
    """
    count: float
    count_source: LimitSource
    used_pct: Optional[float]
    window_s: Optional[float]


@dataclass
class MemoryReading:
    """
    The memory the server's processes may use, and how much of it is in use.

    Args:
        limit_bytes: The limit that binds — the cgroup's where one is set below the machine's,
            otherwise the machine's
        used_bytes: How much of it is in use
        used_pct: The same as a share of the limit
        limit_source: Whether the limit came from the cgroup or the machine
    """
    limit_bytes: int
    used_bytes: int
    used_pct: float
    limit_source: LimitSource

    def free_bytes(self) -> int:
        """
        What the binding limit leaves free, never below zero.

        Returns:
            The limit minus what is in use
        """
        return max(self.limit_bytes - self.used_bytes, 0)


@dataclass
class MachineResourcesReading:
    """
    One reading of the machine's processors and memory.

    Args:
        measured_at: When it was read (UTC) — an observation of our own, from the wall clock
        cpu: The processors
        memory: The memory
    """
    measured_at: datetime
    cpu: CpuReading
    memory: MemoryReading


@dataclass
class DiskReading:
    """
    One filesystem the stores live on, and how much room it still has (#632).

    Measured as a process that is not root may use it: the last 5 to 10 % of a filesystem is
    reserved for root, so a session running as root would write to the very last byte while a
    reading of everything free still looked comfortable. Several stores usually share one
    filesystem, and they are read once, as one reading.

    Args:
        stores: The stores whose root lies on this filesystem, sorted — what makes the reading unique
        device: The filesystem's device number on this machine (`st_dev`) — what a watch keys an
            episode by, since the stores on one filesystem can change while a session runs
        free_bytes: The room a process that is not root may still use
        total_bytes: The filesystem's size
        free_pct: `free_bytes` as a share of `total_bytes`; None for a filesystem that reports no size
    """
    stores: List[StoreId]
    device: int
    free_bytes: int
    total_bytes: int
    free_pct: Optional[float]


@dataclass
class FreeMemoryVerdict:
    """
    Whether the memory a caller asked for is free (#603) — what a wrapper script asks before it
    starts a run.

    Args:
        free_bytes: The binding limit minus what is in use
        required_bytes: What the caller asked to be free
        enough: Whether `free_bytes` reaches `required_bytes`
    """
    free_bytes: int
    required_bytes: int
    enough: bool
