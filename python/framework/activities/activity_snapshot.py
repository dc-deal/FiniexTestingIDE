"""
FiniexTestingIDE - Activity Snapshot
What runs on this machine and what it holds, read at one instant (#603) — for the console list,
the memory check and the API route alike.
"""

from datetime import datetime, timezone

from python.framework.activities.activity_memory_attribution import attribute_memory
from python.framework.activities.activity_registry_reader import ActivityRegistryReader
from python.framework.types.activity_registry_types import ActivitySnapshot
from python.framework.utils.machine_resources_reader import MachineResourcesReader
from python.framework.utils.process_memory_reader import read_process_table


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
