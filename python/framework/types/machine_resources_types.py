"""
FiniexTestingIDE - Machine Resources Types (#595)

What the machine this server runs on has and uses, read at one instant, and how close its memory
is to running out.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Optional


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
