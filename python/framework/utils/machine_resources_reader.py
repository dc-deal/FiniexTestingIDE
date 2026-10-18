"""
FiniexTestingIDE - Machine Resources Reader (#595)

What the machine this server runs on has and uses: the limit that binds its processes, and how
much of it is in use. The binding limit is read the way a container runtime does — the cgroup
first, the machine only where no smaller limit is set — because a container reading the machine's
totals reports memory its processes can never have (#416).

Every read here is a small file in procfs or sysfs, which the kernel answers from memory; nothing
touches the project tree.
"""

import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional, Tuple

import psutil

from python.framework.types.machine_resources_types import (
    CpuReading,
    LimitSource,
    MachineResourcesReading,
    MemoryReading,
)

CGROUP_ROOT = Path('/sys/fs/cgroup')

# A cgroup v1 memory limit at or above this means "no limit": the kernel writes the largest page
# multiple it can hold rather than a marker word, as v2 does with `max`.
_V1_UNLIMITED_BYTES = 1 << 60


class MachineResourcesReader:
    """
    Reads the machine's processors and memory, one reading per call.

    The processors' busy share is a difference between two samples, so the reader keeps the last
    one: the first is taken when it is constructed, and every reading measures since the one
    before it. A lock keeps two callers from measuring the same window twice.

    Args:
        cgroup_root: Where the cgroup filesystem is mounted
        cpu_times_fn: The machine's cumulative processor times
        memory_fn: The machine's memory totals
        usable_cpus_fn: How many processors this process may run on
        clock_fn: A monotonic clock — the window is a duration
    """

    def __init__(
        self,
        cgroup_root: Path = CGROUP_ROOT,
        cpu_times_fn: Callable[[], Any] = psutil.cpu_times,
        memory_fn: Callable[[], Any] = psutil.virtual_memory,
        usable_cpus_fn: Callable[[], int] = lambda: len(os.sched_getaffinity(0)),
        clock_fn: Callable[[], float] = time.monotonic,
    ):
        self._cgroup_root = cgroup_root
        self._cpu_times_fn = cpu_times_fn
        self._memory_fn = memory_fn
        self._usable_cpus_fn = usable_cpus_fn
        self._clock_fn = clock_fn
        self._lock = threading.Lock()
        self._last_sample = (clock_fn(), *_busy_and_total(cpu_times_fn()))

    def read(self) -> MachineResourcesReading:
        """
        Read the processors and the memory now.

        Returns:
            The reading, stamped with when it was taken
        """
        return MachineResourcesReading(
            # Wall clock, and legitimately: it stamps when WE looked, an observation of our own
            measured_at=datetime.now(timezone.utc),
            cpu=self._read_cpu(),
            memory=self._read_memory(),
        )

    def _read_cpu(self) -> CpuReading:
        """
        The processors that bind, and how busy the machine was since the previous reading.

        Returns:
            The processor reading; its busy share is None when no time has passed to measure
        """
        quota = self._cgroup_cpu_quota()
        usable = float(self._usable_cpus_fn())
        if quota is not None and quota < usable:
            count, count_source = quota, LimitSource.CGROUP
        else:
            count, count_source = usable, LimitSource.MACHINE

        # Sampled under the lock, so two callers' samples are stored in the order they were taken
        with self._lock:
            now = self._clock_fn()
            busy, total = _busy_and_total(self._cpu_times_fn())
            last_at, last_busy, last_total = self._last_sample
            self._last_sample = (now, busy, total)
        spent = total - last_total
        if spent <= 0:
            return CpuReading(count=count, count_source=count_source, used_pct=None, window_s=None)
        return CpuReading(
            count=count,
            count_source=count_source,
            used_pct=round((busy - last_busy) / spent * 100.0, 1),
            window_s=round(now - last_at, 2),
        )

    def _read_memory(self) -> MemoryReading:
        """
        The memory limit that binds, and how much of it is in use.

        Returns:
            The memory reading — the cgroup's where it is set below the machine's
        """
        machine = self._memory_fn()
        limit_and_used = self._cgroup_memory()
        if limit_and_used is not None and limit_and_used[0] < machine.total:
            limit, used = limit_and_used
            source = LimitSource.CGROUP
        else:
            limit, used = machine.total, machine.total - machine.available
            source = LimitSource.MACHINE
        return MemoryReading(
            limit_bytes=int(limit),
            used_bytes=int(used),
            used_pct=round(used / limit * 100.0, 1) if limit > 0 else 0.0,
            limit_source=source,
        )

    def _cgroup_memory(self) -> Optional[Tuple[int, int]]:
        """
        The cgroup's memory limit and use, cgroup v2 first, v1 second.

        Returns:
            (limit, used) in bytes, or None when no limit is set or none can be read
        """
        limit_text = _read_text(self._cgroup_root / 'memory.max')
        if limit_text is not None:
            if limit_text == 'max':
                return None
            used_text = _read_text(self._cgroup_root / 'memory.current')
            return (int(limit_text), int(used_text)) if used_text is not None else None

        v1 = self._cgroup_root / 'memory'
        limit_text = _read_text(v1 / 'memory.limit_in_bytes')
        used_text = _read_text(v1 / 'memory.usage_in_bytes')
        if limit_text is None or used_text is None or int(limit_text) >= _V1_UNLIMITED_BYTES:
            return None
        return int(limit_text), int(used_text)

    def _cgroup_cpu_quota(self) -> Optional[float]:
        """
        The cgroup's processor quota, cgroup v2 first, v1 second.

        Returns:
            The quota in processors — fractional where it is — or None when none is set
        """
        quota_text = _read_text(self._cgroup_root / 'cpu.max')
        if quota_text is not None:
            quota, _, period = quota_text.partition(' ')
            if quota == 'max' or not period:
                return None
            return int(quota) / int(period)

        v1 = self._cgroup_root / 'cpu'
        quota_text = _read_text(v1 / 'cpu.cfs_quota_us')
        period_text = _read_text(v1 / 'cpu.cfs_period_us')
        if quota_text is None or period_text is None or int(quota_text) <= 0:
            return None
        return int(quota_text) / int(period_text)


def _busy_and_total(times: Any) -> Tuple[float, float]:
    """
    The busy and the total processor time in one cumulative sample.

    Args:
        times: A cumulative sample — idle, iowait and the busy kinds, in seconds

    Returns:
        (busy, total) seconds. Guest time is already counted inside user and nice on Linux, so it
        is taken out of the total rather than counted twice
    """
    total = sum(times) - getattr(times, 'guest', 0.0) - getattr(times, 'guest_nice', 0.0)
    idle = times.idle + getattr(times, 'iowait', 0.0)
    return total - idle, total


def _read_text(path: Path) -> Optional[str]:
    """
    One small kernel file, stripped.

    Args:
        path: The file

    Returns:
        Its content, or None when it is absent or unreadable
    """
    try:
        return path.read_text().strip()
    except OSError:
        return None
