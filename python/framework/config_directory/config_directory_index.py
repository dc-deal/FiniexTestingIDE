"""
FiniexTestingIDE - Config Directory Index (#554)

The directory's cache: one row per configuration file, keyed on the file's path, modification
time and size. A DERIVED store in the §44 sense — deleting it costs one re-read of every file and
loses nothing, because every row is a function of a file that still exists.

The row itself travels as its JSON rather than as one column per field: the served model is the
unit this index caches, and a column per field would be a second schema to keep in step with it.
A change to what a row MEANS bumps `LOGIC_VERSION`, and the base class turns that into a re-read.
"""

from pathlib import Path

from python.framework.store.abstract_store_index import AbstractStoreIndex, store_index_filename
from python.framework.types.store_types import StoreId

CONFIG_DIRECTORY_INDEX_FILE = store_index_filename(StoreId.CONFIG_DIRECTORY)


class ConfigDirectoryIndex(AbstractStoreIndex):
    """
    The cached reading of every configuration file the directory walk found.

    Args:
        root: The store's directory; the index file lives at its root (§44)
    """

    COLUMNS = ['path', 'source_mtime', 'source_size', 'status', 'row_json']
    # 1 → 2: the builder reads `scenario_name` / `profile_name` (2026-09-28); a row cached by the
    # older logic read the retired `name` keys and is re-read rather than trusted.
    LOGIC_VERSION = 2

    def __init__(self, root: Path):
        super().__init__(Path(root) / CONFIG_DIRECTORY_INDEX_FILE)

    def rebuild(self) -> int:
        """
        Drop the cache so the next refresh reads every file again.

        The rows are a function of the configuration files, and only the directory's refresh knows
        where those are — so a rebuild here is a delete, and the refresh that follows is the
        re-read.

        Returns:
            0 — nothing is indexed until the next refresh
        """
        self.get_path().unlink(missing_ok=True)
        return 0
