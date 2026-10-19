"""
FiniexTestingIDE - Free Memory Validator (#603)

Whether the memory a run is about to need is free — the question a wrapper script asks before it
starts one. A verdict, so it lives here; the console only reports it. Free is the binding limit
minus what is in use, the same reading the machine route serves.
"""

from python.framework.types.machine_resources_types import FreeMemoryVerdict, MemoryReading

_GIB = 1024 ** 3


def judge_free_memory(memory: MemoryReading, min_free_gb: float) -> FreeMemoryVerdict:
    """
    Judge whether at least `min_free_gb` of memory is free.

    Args:
        memory: The machine's memory reading
        min_free_gb: What the caller needs free, in GiB

    Returns:
        The verdict with both figures
    """
    free_bytes = max(memory.limit_bytes - memory.used_bytes, 0)
    required_bytes = int(min_free_gb * _GIB)
    return FreeMemoryVerdict(free_bytes=free_bytes, required_bytes=required_bytes,
                             enough=free_bytes >= required_bytes)
