"""
FiniexTestingIDE - Session Store Roots (#632)

The roots an AutoTrader session writes its records and its carry-over into — the one answer the check
before the start, the watch over the free space and the machine's live view ask. Taken from the store
catalog, so a root that moves in configuration moves here too; the two carry-overs from the profile,
which may place them elsewhere than the application's defaults.
"""

from pathlib import Path
from typing import Dict, Optional

from python.configuration.data_home import resolve_in_data_home
from python.framework.store.store_registrations import build_registrations
from python.framework.types.store_types import StoreId

# What a session writes: its run directory and the run index, its ledger rows, its frozen
# configuration and the patch of uncommitted code, the global log, its entry in the machine's list
# of activities, the broker configuration it fetched — and its two carry-overs
SESSION_STORES = (
    StoreId.RUNS,
    StoreId.RUN_LEDGER,
    StoreId.RUN_CONFIGS,
    StoreId.RUN_PATCHES,
    StoreId.GLOBAL_LOG,
    StoreId.ACTIVITIES,
    StoreId.BROKER_RUNTIME,
    StoreId.COLD_START_STATE,
    StoreId.SESSION_STATE,
)


def session_store_roots(cold_start_path: Optional[str] = None,
                        state_persistence_path: Optional[str] = None) -> Dict[StoreId, Path]:
    """
    Every root a session writes into.

    Args:
        cold_start_path: The profile's `cold_start.path`; the catalog's root when None
        state_persistence_path: The profile's `state_persistence.path`; the catalog's root when None

    Returns:
        Each written store's root
    """
    registrations = build_registrations()
    roots = {store: registrations[store].root for store in SESSION_STORES}
    # The global log's root is its file; what is written into is its folder
    roots[StoreId.GLOBAL_LOG] = roots[StoreId.GLOBAL_LOG].parent
    if cold_start_path is not None:
        roots[StoreId.COLD_START_STATE] = resolve_in_data_home(Path(cold_start_path))
    if state_persistence_path is not None:
        roots[StoreId.SESSION_STATE] = resolve_in_data_home(Path(state_persistence_path))
    return roots
