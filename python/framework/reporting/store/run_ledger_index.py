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

    # 3 (#497): the column is now `account_max_drawdown` and it holds a different measure
    # than the `max_drawdown` before it. Two changes in one version, because they shipped
    # together:
    #   - The MEASURE: the largest decline of the EQUITY CURVE, sampled on every TICK in
    #     both pipelines. It used to be the largest decline across CLOSED TRADES, which is
    #     one of the two things Pardo names under the one word and not the one people mean.
    #   - The NAME: `account_` says which of the three drawdowns this is — the account's,
    #     not a single trade's excursion (`mae_pnl`) and not the safety reading against a
    #     configured baseline (`SafetyConfig.max_drawdown_pct`, a threshold).
    # Fragments written before this version carry the old column name and were renamed in
    # place by `python/experiments/migrate_ledger_drawdown_column.py`; their VALUES are
    # still the old measure, so a sweep must not rank across the boundary.
    #
    # 3 → 4 (#518): six columns appended saying WHICH DATA a row was produced over. Unlike the
    # rename above this changes no existing value, so ranking across the boundary stays valid —
    # what an older fragment cannot do is answer the question at all, and it reads back as None
    # rather than as a made-up answer.
    #
    # 4 → 5 (#520 / §31c): `price_bases` appended — WHICH PRICE the bars a row was produced
    # over were rendered from. Same shape as 3 → 4: one column, no existing value changes, so
    # ranking across the boundary stays valid. It is its own column rather than part of
    # `data_format_versions` because the two answer different questions and come from
    # different archives — the format version from the tick files, the basis from the bar
    # files, which are the only ones that stamp it.
    #
    # 5 → 6 (#497): `max_equity` and `account_max_drawdown_pct` appended. The percentage is not
    # derivable from the amount and the peak — it was measured against the peak standing at the
    # time — so without the column the ledger holds a drawdown it cannot express as a share.
    # Same shape again: appended, no existing value changes.
    LOGIC_VERSION: int = 6

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
