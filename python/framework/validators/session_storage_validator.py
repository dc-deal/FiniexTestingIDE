"""
FiniexTestingIDE - Session Storage Validator (#632)

Before a session's first order: every root it writes into can be written, and every filesystem those
roots live on has room above the configured floor. A session that cannot record what it does would
trade without a record, and the first sign of a full disk would be the first failed write.

Writability is proven by writing, not by asking: `os.access` and a permission bit answer yes to root
on a read-only folder, and say nothing of a network mount, a quota or a container's own refusal. So
each root gets a probe file of one page — data, not only a folder entry, so a block quota refuses it
too — removed again at once; a probe a crash left behind is replaced and removed by the next start.
Its name ends in no suffix a store reads, so no store mistakes it for an entry. Every root and every
filesystem that fails is named in one refusal, so the operator fixes them all before the next start.
"""

from pathlib import Path
from typing import Dict, List, Optional

from python.framework.types.store_types import StoreId
from python.framework.utils.file_utils import create_directory
from python.framework.utils.machine_resources_reader import MachineResourcesReader

# The probe each root is written with, beside the store's entries, and its size: one page of data
WRITE_PROBE_NAME = '.finiex_write_probe'
WRITE_PROBE_BYTES = 4096

_BYTES_PER_GB = 1024 ** 3


def check_session_storage(roots: Dict[StoreId, Path], min_free_gb: float,
                          reader: MachineResourcesReader) -> Optional[str]:
    """
    Whether a session can record: every root writable, every filesystem above the floor.

    Args:
        roots: Each store the session writes into, with its root
        min_free_gb: The free space each filesystem must have (`autotrader.storage.min_free_gb_to_start`)
        reader: The machine's reader, which measures the filesystems

    Returns:
        Why the session cannot start — every store that cannot be written and every filesystem
        below the floor, each with its cause; None when it can
    """
    refusals: List[str] = []
    for store, root in sorted(roots.items(), key=lambda item: item[0].value):
        probe = root / WRITE_PROBE_NAME
        try:
            create_directory(root, holds=f'the {store.value} store')
            probe.write_bytes(bytes(WRITE_PROBE_BYTES))
            probe.unlink()
        except OSError as error:
            refusals.append(f'the {store.value} store {root.absolute()} cannot be written '
                            f'({type(error).__name__}: {error})')

    floor = min_free_gb * _BYTES_PER_GB
    for reading in reader.read_disks(roots):
        if reading.free_bytes < floor:
            stores = ', '.join(store.value for store in reading.stores)
            refusals.append(f'the disk under {roots[reading.stores[0]].absolute()} (the {stores} '
                            f'store(s)) has {reading.free_bytes / _BYTES_PER_GB:.1f} GB free, below '
                            f'autotrader.storage.min_free_gb_to_start ({min_free_gb:g} GB) — the '
                            f"session's records would stop when it is full")
    return '; '.join(refusals) if refusals else None
