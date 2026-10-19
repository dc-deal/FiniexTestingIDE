"""
FiniexTestingIDE - Activity Snapshot
What runs on this machine and what it holds, read at one instant (#603) — for the console list,
the memory check and the API route alike.
"""

from datetime import datetime, timezone

from python.framework.activities.activity_memory_attribution import attribute_memory
from python.framework.activities.activity_registry_reader import ActivityRegistryReader
from python.framework.types.activity_registry_types import ActivitySnapshot, FreeMemoryCheck
from python.framework.utils.machine_resources_reader import MachineResourcesReader
from python.framework.utils.process_memory_reader import read_process_table
from python.framework.validators.free_memory_validator import judge_free_memory


def take_activity_snapshot(
    reader: ActivityRegistryReader,
    machine_reader: MachineResourcesReader,
) -> ActivitySnapshot:
    """
    Read the registry, the process table and the machine's memory, in that order.

    Args:
        reader: The registry's reader
        machine_reader: The machine's reader; only its memory is read, so its processor window
            is left as it was

    Returns:
        The snapshot
    """
    # Wall clock, and legitimately: it stamps when we looked, an observation of our own
    measured_at = datetime.now(timezone.utc)
    listing = reader.read()
    memory = attribute_memory(listing.entries, read_process_table())
    return ActivitySnapshot(
        measured_at=measured_at,
        listing=listing,
        memory=memory,
        machine_memory=machine_reader.read_memory(),
    )


def take_memory_check(
    reader: ActivityRegistryReader,
    machine_reader: MachineResourcesReader,
    required_bytes: int,
) -> FreeMemoryCheck:
    """
    Whether `required_bytes` are free, judged from the machine's memory alone; when they are not,
    who holds the memory, read beside the answer.

    A list that cannot be read never changes the answer: a script that waits for memory must not
    wait for a readable list.

    Args:
        reader: The registry's reader
        machine_reader: The machine's reader
        required_bytes: What the caller needs free

    Returns:
        The verdict, and when it is no, the reading of who holds the memory or why it failed
    """
    verdict = judge_free_memory(machine_reader.read_memory(), required_bytes)
    if verdict.enough:
        return FreeMemoryCheck(verdict=verdict)
    try:
        return FreeMemoryCheck(verdict=verdict,
                               snapshot=take_activity_snapshot(reader, machine_reader))
    except OSError as error:
        return FreeMemoryCheck(verdict=verdict, list_error=error.strerror or type(error).__name__)
