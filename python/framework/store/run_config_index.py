"""
FiniexTestingIDE - Run Config Index (#538)

The read path over the registered run configurations.

Two questions are answered from this one file, and both used to cost a directory walk. **Which
file is called `x`** — asked on every run start and on every listing, and answered today by a
recursive glob over `user_algos/`, which measured 11.6 s of a 19.5 s scenario listing (§42: a
directory walk costs 282x on this tree). And **what versions has `x` had** — which nothing could
answer at all, because a config that changed left no trace.

Several rows per `source_name` are the NORMAL case here, unlike every other index in this
project: each row is one version, and that is the history. Validity is therefore not a row count
against a file count; it is whether the sources still look the way they did when they were
registered.
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from python.framework.store.abstract_store_index import (
    AbstractStoreIndex,
    store_index_filename,
)
from python.framework.types.run_config_types import RunConfigEntry, RunConfigKind
from python.framework.types.store_types import StoreId

RUN_CONFIG_INDEX_FILE = store_index_filename(StoreId.RUN_CONFIGS)

# Where the frozen copies live, one subdirectory per kind — so a human opening the store sees
# what it holds without reading the index.
FROZEN_SUBDIR: Dict[RunConfigKind, str] = {
    RunConfigKind.SCENARIO_SET: 'scenario_sets',
    RunConfigKind.AUTOTRADER_PROFILE: 'autotrader_profiles',
}


class RunConfigIndex(AbstractStoreIndex):
    """
    One row per registered configuration VERSION.

    Args:
        root: The `run_configs` directory
    """

    COLUMNS: List[str] = [
        'config_id', 'kind', 'frozen_file',
        'source_name', 'source_path', 'source_mtime', 'source_size',
        'first_seen', 'last_seen', 'param_hash', 'scope_hash', 'run_count',
    ]
    LOGIC_VERSION: int = 1

    def __init__(self, root: Path):
        super().__init__(Path(root) / RUN_CONFIG_INDEX_FILE)
        self._root = Path(root)

    def get_root(self) -> Path:
        """
        The store's directory.

        Returns:
            The root this index describes
        """
        return self._root

    def frozen_files(self) -> List[Path]:
        """
        Every frozen copy below the root.

        Returns:
            Sorted paths; empty when the store does not exist yet
        """
        if not self._root.exists():
            return []
        return sorted(p for name in FROZEN_SUBDIR.values()
                      for p in (self._root / name).glob('*.json'))

    def staleness_reason(self) -> Optional[str]:
        """
        Why the index may not be served.

        Deliberately NOT a row count against a file count — several rows per source file are the
        normal case here, because each one is a version. What must hold is that every row still
        points at a frozen copy that exists: a missing copy means the store was pruned by hand,
        and the index then describes bytes nobody can read back.

        The SOURCE files are not checked here. They are checked where it matters and where the
        cost is known — one stat per known path at resolution time, measured at 111 ms for 67
        configs against 613 ms for a single recursive glob.

        Returns:
            The reason, or None when the index still describes the store
        """
        code = super().staleness_reason()
        if code is not None:
            return code

        frame = self.read()
        if frame.empty:
            return None
        present = {p.name for p in self.frozen_files()}
        missing = [name for name in frame['frozen_file'] if name not in present]
        if missing:
            return f'{len(missing)} indexed version(s) have no frozen copy on disk'
        return None

    def rebuild(self) -> int:
        """
        Rebuild what the frozen copies can still say for themselves.

        A rebuild here is LOSSY and says so rather than pretending otherwise: the frozen bytes
        carry the identity and the content, but `first_seen`, `source_path` and `run_count` are
        observations made at registration time and exist nowhere else. They come back empty, and
        the history collapses to "these versions exist" without their dates.

        That is the honest shape for a store whose index is not purely derived. It is also why
        the index is written incrementally on every registration instead of being rebuilt: the
        rebuild is the repair path, not the write path.

        Returns:
            Number of rows written — one per frozen copy found
        """
        rows = []
        for kind, subdir in FROZEN_SUBDIR.items():
            for path in sorted((self._root / subdir).glob('*.json')):
                stamp = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
                rows.append({
                    'config_id': path.stem, 'kind': str(kind), 'frozen_file': path.name,
                    'source_name': '', 'source_path': '', 'source_mtime': 0.0,
                    'source_size': 0, 'first_seen': stamp, 'last_seen': stamp,
                    'param_hash': '', 'scope_hash': None, 'run_count': 0,
                })
        self.write(pd.DataFrame(rows, columns=self.COLUMNS))
        return len(rows)

    def entries(self) -> List[RunConfigEntry]:
        """
        Every registered version, newest first.

        Returns:
            One entry per row
        """
        frame = self.read()
        if frame.empty:
            return []
        frame = frame.sort_values('first_seen', ascending=False)
        return [_to_entry(r) for r in frame.itertuples()]

    def versions_of(self, source_name: str) -> List[RunConfigEntry]:
        """
        Every registered version of one source file, oldest first — the history.

        Args:
            source_name: The file name as a caller asks for it

        Returns:
            Its versions in the order they were first seen
        """
        frame = self.read()
        if frame.empty:
            return []
        rows = frame[frame['source_name'] == source_name].sort_values('first_seen')
        return [_to_entry(r) for r in rows.itertuples()]

    def current_of(self, source_name: str) -> Optional[RunConfigEntry]:
        """
        The version of a source file that was registered most recently.

        Args:
            source_name: The file name as a caller asks for it

        Returns:
            Its newest entry, or None when the name was never registered
        """
        versions = self.versions_of(source_name)
        return versions[-1] if versions else None

    def by_id(self, config_id: str) -> Optional[RunConfigEntry]:
        """
        One version by its identity.

        Args:
            config_id: The content id

        Returns:
            The entry, or None when the id is unknown
        """
        frame = self.read()
        if frame.empty:
            return None
        rows = frame[frame['config_id'] == config_id]
        return _to_entry(next(rows.itertuples())) if not rows.empty else None

    def upsert(self, entry: RunConfigEntry) -> None:
        """
        Write one version's row, replacing an earlier row for the same id.

        The id is the key, not the source name: re-registering an unchanged file updates
        `last_seen` and `source_path` on the SAME row, while a changed file adds a row beside it.
        That is what makes the history accumulate without a second mechanism.

        Args:
            entry: The version to record
        """
        frame = self.read()
        if not frame.empty:
            frame = frame[frame['config_id'] != entry.config_id]
        row = pd.DataFrame([{
            'config_id': entry.config_id, 'kind': str(entry.kind),
            'frozen_file': entry.frozen_file, 'source_name': entry.source_name,
            'source_path': entry.source_path, 'source_mtime': entry.source_mtime,
            'source_size': entry.source_size, 'first_seen': entry.first_seen,
            'last_seen': entry.last_seen, 'param_hash': entry.param_hash,
            'scope_hash': entry.scope_hash, 'run_count': entry.run_count,
        }])
        self.write_incremental(pd.concat([frame, row], ignore_index=True))


def _to_entry(row) -> RunConfigEntry:
    """
    One index row as a typed entry.

    Args:
        row: A pandas itertuples row

    Returns:
        The entry
    """
    scope = getattr(row, 'scope_hash', None)
    return RunConfigEntry(
        config_id=row.config_id, kind=RunConfigKind(row.kind), frozen_file=row.frozen_file,
        source_name=row.source_name, source_path=row.source_path,
        source_mtime=float(row.source_mtime), source_size=int(row.source_size),
        first_seen=row.first_seen, last_seen=row.last_seen,
        param_hash=row.param_hash or '',
        scope_hash=scope if isinstance(scope, str) and scope else None,
        run_count=int(row.run_count),
    )
