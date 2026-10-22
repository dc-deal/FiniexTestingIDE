"""
FiniexTestingIDE - Free Memory Validator (#603)

Whether the memory a run is about to need is free — the question a wrapper script asks before it
starts one. A verdict, so it lives here; the console only reports it. Free is the binding limit
minus what is in use, the same reading the machine route serves.
"""

from python.framework.types.machine_resources_types import FreeMemoryVerdict, MemoryReading


def judge_free_memory(memory: MemoryReading, required_bytes: int) -> FreeMemoryVerdict:
    """
    Judge whether at least `required_bytes` of memory are free.

    Args:
        memory: The machine's memory reading
        required_bytes: What the caller needs free

    Returns:
        The verdict with both figures
    """
    free_bytes = memory.free_bytes()
    return FreeMemoryVerdict(free_bytes=free_bytes, required_bytes=required_bytes,
                             enough=free_bytes >= required_bytes)
