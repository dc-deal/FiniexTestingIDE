"""
FiniexTestingIDE - Process Memory Reader
What the processes on this machine hold, read in one pass (#603).

One pass over the process table yields every process's parent and resident memory, and the memory
of a process tree is then a walk over that table — never a second read per process. Measured on
2026-10-18 in this container: one pass over about 670 processes took 40-60 ms, while asking a
single process for its children took 15-22 ms per call.

Resident memory counts every page a process maps, the ones it shares included — its libraries, and
what a forked child still shares with its parent — so a sum over processes is an upper bound.

The calling process can also ask for itself, private pages included (`read_own_memory`) — what the
state frames record (#579, #611).

Not this module: the machine's own memory and load, which `machine_resources_reader` reads.
"""

from typing import Dict, Optional

import psutil

from python.framework.types.process_table_types import OwnProcessMemory, ProcessReading


def read_process_table() -> Dict[int, ProcessReading]:
    """
    Every process of the machine with its parent and its resident memory.

    Returns:
        The readings keyed by process id
    """
    table: Dict[int, ProcessReading] = {}
    for process in psutil.process_iter(['pid', 'ppid', 'memory_info']):
        info = process.info
        memory = info.get('memory_info')
        table[info['pid']] = ProcessReading(
            pid=info['pid'],
            ppid=info.get('ppid') or 0,
            rss_bytes=memory.rss if memory is not None else None,
        )
    return table


def read_process_start(pid: int) -> Optional[float]:
    """
    When a living process was started, as the operating system reports it.

    A zombie — a process that has ended and waits for its parent to collect it — counts as gone:
    it runs nothing and holds no memory. Measured on 2026-10-19 in this container, whose first
    process does not collect orphans: an API server killed together with its shell stayed in the
    process table as a zombie, start time and all.

    Args:
        pid: The process id

    Returns:
        The start as seconds since the epoch, or None when no such process lives
    """
    try:
        process = psutil.Process(pid)
        if process.status() == psutil.STATUS_ZOMBIE:
            return None
        return process.create_time()
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return None


def read_own_memory() -> OwnProcessMemory:
    """
    What the calling process holds — one process, read for itself, never a pass over the table.

    The resident size is cheap; the private pages need the process's full memory map. Measured on
    2026-10-18 (#611): 13.5 µs and 146 µs — affordable once a minute, not on every tick.

    Returns:
        The reading; a figure the system would not give is None
    """
    process = psutil.Process()
    rss_bytes: Optional[int] = None
    private_bytes: Optional[int] = None
    try:
        rss_bytes = process.memory_info().rss
    except psutil.Error:
        pass
    try:
        private_bytes = process.memory_full_info().uss
    except (psutil.Error, AttributeError):
        # AttributeError: a platform whose full memory info carries no unique set size
        pass
    return OwnProcessMemory(rss_bytes=rss_bytes, private_bytes=private_bytes)
