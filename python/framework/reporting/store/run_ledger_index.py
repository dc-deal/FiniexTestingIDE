"""
Run-ledger index (#486) — the read shape of the run-results ledger.

One fragment per run is the right WRITE shape: parquet is immutable, so a file per run is the
lock-free append. It is the wrong READ shape — measured here, 404 fragments cost 3.29 s to open
while the same rows as a single file cost 0.008 s, and 99.6 % of that is the open rather than
the work. This index gives the read path one file without giving up the append property.

For a set-shaped store the index and the compaction COINCIDE: what a caller wants is every row,
so the derived file holds every row rather than a pointer table.
"""

from pathlib import Path
from typing import List, Optional

import pandas as pd

from python.framework.store.abstract_store_index import (
    AbstractStoreIndex,
    store_index_filename,
)
from python.framework.types.store_types import StoreId

LEDGER_INDEX_FILE = store_index_filename(StoreId.RUN_LEDGER)


class RunLedgerIndex(AbstractStoreIndex):
    """
    The union of every ledger fragment, kept as one file.

    Args:
        ledger_dir: The directory holding the per-run fragments
        columns: The ledger's canonical column set
    """

    LOGIC_VERSION: int = 2

    def __init__(self, ledger_dir: Path, columns: List[str]):
        super().__init__(Path(ledger_dir) / LEDGER_INDEX_FILE)
        self._dir = Path(ledger_dir)
        self.COLUMNS = list(columns)

    def fragments(self) -> List[Path]:
        """
        The ledger's fragment files, index and leftovers excluded.

        Dot-files are skipped as well as the index itself: a superseded derived file left in the
        directory must never be mistaken for a run's record.

        Returns:
            Sorted fragment paths; empty when the ledger does not exist yet
        """
        if not self._dir.exists():
            return []
        return sorted(f for f in self._dir.glob('*.parquet')
                      if f.name != LEDGER_INDEX_FILE and not f.name.startswith('.'))

    def staleness_reason(self) -> Optional[str]:
        """
        Why the index may not be served.

        Two questions, and the second is the one a plain mtime rule misses: the fragments are
        append-only, so "newer than every fragment" settles freshness against the DATA — but a
        changed column set is a change in the CODE, invisible to any mtime.

        Returns:
            The reason, or None when the index is valid
        """
        code = super().staleness_reason()
        if code is not None:
            return code
        fragments = self.fragments()
        if not fragments:
            return None
        newest = max(f.stat().st_mtime_ns for f in fragments)
        if self.get_path().stat().st_mtime_ns < newest:
            return f'{len(fragments)} fragment(s) on disk, some newer than the index'
        return None

    def rebuild(self) -> int:
        """
        Rebuild the union from the fragments.

        Fragments are read INDIVIDUALLY rather than as one directory: one written before a column
        existed simply lacks it, and a directory read would collapse to a common schema and
        silently drop the newer columns. reindex pins the canonical set.

        Returns:
            How many rows the ledger holds
        """
        fragments = self.fragments()
        if not fragments:
            self.write(pd.DataFrame(columns=self.COLUMNS))
            return 0
        frame = pd.concat([pd.read_parquet(f) for f in fragments], ignore_index=True)
        frame = frame.reindex(columns=self.COLUMNS)
        self.write(frame)
        return len(frame)
