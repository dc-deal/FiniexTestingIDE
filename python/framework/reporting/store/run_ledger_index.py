"""
Run-ledger index (#486) — the read shape of the run-results ledger.

One fragment per run is the right WRITE shape: parquet is immutable, so a file per run is the
lock-free append. It is the wrong READ shape — measured here, 404 fragments cost 3.29 s to open
while the same rows as a single file cost 0.008 s, and 99.6 % of that is the open rather than
the work. This index gives the read path one file without giving up the append property.

For a set-shaped store the index and the compaction COINCIDE: what a caller wants is every row,
so the derived file holds every row rather than a pointer table.
"""

import os
import time
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
    #
    # 6 → 7 (#497): `deployment_id` and `profile_hash` appended. The first is the join key that
    # makes a restarted live bot readable as ONE history; the second is the fingerprint over the
    # operational half of the profile, which `param_hash` deliberately does not cover — a raised
    # stop level must not read as a different strategy. Appended, no existing value changes; an
    # older fragment reads back None for both, which is honest: no live row before this version
    # ever carried a deployment.
    #
    # 7 → 8 (#390): `run_type` appended — WHICH PIPELINE wrote the row, from the same two
    # constants the run tree is laid out with. Before it, telling a backtest from a live
    # session meant reading `input_plane`, which answers a different question and is empty on
    # everything written before #518: measured 2026-09-18, 520 of 616 rows could not say what
    # they were. Backfilled where it could be resolved rather than left blank
    # (`python/experiments/backfill_ledger_run_type.py`), so this is the one appended column
    # whose older rows DO carry an answer — and the ones that could not be resolved were
    # removed rather than guessed.
    #
    # 8 → 9 (#497): `recorded_at_utc` appended — when the row was written, which is within
    # seconds of when its run ended. The ledger had no end of any kind, so the only measurable
    # gap between two sessions of a deployment ran from START to START and counted the
    # previous session's whole runtime as downtime. Appended, no existing value changes; an
    # older row reads back empty and its gap falls back to the old measure, labelled.
    # 9 → 10 (#537): `gross_profit` / `gross_loss` appended, the two halves `profit_factor` is
    # the quotient OF — without them a rate cannot be recovered from the rows it was folded out
    # of, which is what a booking journal has to do. Nine columns that used to read back a
    # measured 0.0 where nothing had been measured became nullable in the same step:
    # `final_equity` was absent on 503 of 564 fragments and read as a balance of zero.
    #
    # 10 → 11 (#32): `trial_count` appended — how many candidates a run was selected from. Same
    # shape as every append above: no existing value changes, so ranking across the boundary
    # stays valid, and an older fragment answers None rather than a made-up number.
    LOGIC_VERSION: int = 11

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
        # The instant the READ begins, not the one the write ends at. Reading 564 fragments
        # took 3.5 s on a copy and 6.4 s on this mount, and a fragment written inside that
        # window is not in the result — while the finished file's own mtime would be NEWER
        # than it, so `staleness_reason` below would call the index fresh and the fragment
        # would stay invisible until something else forced a rebuild. Stamping the start
        # makes the mtime mean "the store as of this instant", which is what the comparison
        # already assumes, and the missed fragment shows up as stale on the next read.
        read_started_ns = time.time_ns()
        fragments = self.fragments()
        if not fragments:
            self.write(pd.DataFrame(columns=self.COLUMNS))
            self._stamp(read_started_ns)
            return 0
        frame = pd.concat([pd.read_parquet(f) for f in fragments], ignore_index=True)
        frame = frame.reindex(columns=self.COLUMNS)
        self.write(frame)
        self._stamp(read_started_ns)
        return len(frame)

    def _stamp(self, read_started_ns: int) -> None:
        """
        Date the index by when its read began.

        Args:
            read_started_ns: The instant the fragment read started
        """
        path = self.get_path()
        os.utime(path, ns=(read_started_ns, read_started_ns))
