"""
Discovery-cache index (#486) — the read path over the discovery cache families.

The caches are DERIVED and therefore disposable, but "disposable" is not the same as
"unfindable": until now, knowing WHAT is cached meant listing the directory, which is one file
operation per entry on a mount where the operation itself is the cost. This index answers that
from one file, across every family at once, and absorbs the family #195 adds without a new
listing path.

It answers WHAT IS THERE, not WHETHER IT IS STILL GOOD. Each family's `get_cache_status()`
computes per-symbol validity against the bar index — a different question with a different
input, and this index deliberately does not pretend to replace it.
"""

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from python.framework.store.abstract_store_index import (
    AbstractStoreIndex,
    store_index_filename,
)
from python.framework.types.store_types import StoreId
from python.framework.utils.config_fingerprint_utils import read_fingerprint_from_parquet

DISCOVERY_INDEX_FILE = store_index_filename(StoreId.DISCOVERY_CACHES)


class DiscoveryCacheIndex(AbstractStoreIndex):
    """
    One row per cached discovery artifact, across every family.

    Args:
        cache_root: The `discovery_caches` directory
    """

    COLUMNS: List[str] = [
        'family', 'file', 'path', 'rows', 'size_bytes', 'modified_at', 'config_fingerprint',
    ]
    LOGIC_VERSION: int = 1

    def __init__(self, cache_root: Path):
        super().__init__(Path(cache_root) / DISCOVERY_INDEX_FILE)
        self._root = Path(cache_root)

    def cache_files(self) -> List[Path]:
        """
        Every cached artifact below the root, the index itself excluded.

        Returns:
            Sorted cache file paths; empty when the root does not exist
        """
        if not self._root.exists():
            return []
        return sorted(f for f in self._root.glob('**/*.parquet')
                      if f.name != DISCOVERY_INDEX_FILE)

    def staleness_reason(self) -> Optional[str]:
        """
        Why the index may not be served.

        The COUNT is checked as well as the timestamps, and that is not belt-and-braces: a family
        clearing or rebuilding its caches deletes files, and deletion leaves every surviving
        file's mtime untouched. A purely time-based rule would keep reporting entries that are
        gone.

        Returns:
            The reason, or None when the index still describes the cache directory
        """
        code = super().staleness_reason()
        if code is not None:
            return code
        files = self.cache_files()
        rows = len(self.read())
        if rows != len(files):
            return f'{rows} indexed vs {len(files)} cache file(s) on disk'
        if files and self.get_path().stat().st_mtime_ns < max(f.stat().st_mtime_ns for f in files):
            return 'cache files were rebuilt after the index — run `store_cli.py rebuild discovery_caches`'
        return None

    def rebuild(self) -> int:
        """
        Rebuild the index from the cache files.

        Row counts come from the parquet FOOTER rather than by loading the frame — the footer
        answers `num_rows` without reading a column, which is what makes a rebuild over every
        family affordable.

        Returns:
            How many cache files were indexed
        """
        rows = [_index_row(path) for path in self.cache_files()]
        self.write(pd.DataFrame(rows, columns=self.COLUMNS))
        return len(rows)

    def write_cache_file(self, table: pa.Table, cache_path: Path) -> None:
        """
        Write one family's cache file and enter it in the index — the one way a family writes.

        A family that writes a cache on demand — a report computed because its cache was invalid —
        would otherwise leave the index describing the generation before it, and the catalog
        would report a stale index nobody asked to rebuild. Written beside its place and then
        moved there, so a reader never opens half a file.

        Args:
            table: The cache content, its metadata included
            cache_path: Where the family keeps it
        """
        tmp_path = cache_path.with_suffix('.parquet.tmp')
        pq.write_table(table, tmp_path)
        os.replace(tmp_path, cache_path)
        self._record_file(cache_path)

    def _record_file(self, path: Path) -> None:
        """
        Enter one cache file just written, replacing its earlier row.

        One row costs a read and a write of this file (about 45 ms measured 2026-10-16) where a
        full rebuild opens every cache file (about 850 ms). It does not repair an index that was
        already behind for other files: the count check still says so.

        Args:
            path: The cache file just written
        """
        path = Path(path)
        current = self.read()
        kept = current[current['path'] != str(path)] if not current.empty else current
        row = pd.DataFrame([_index_row(path)], columns=self.COLUMNS)
        frame = row if kept.empty else pd.concat([kept, row], ignore_index=True)
        self.write_incremental(frame)


def _index_row(path: Path) -> Dict:
    """
    One cache file's index row.

    Row counts come from the parquet footer rather than by loading the frame.

    Args:
        path: The cache file

    Returns:
        The row, keyed by the index's columns
    """
    stat = path.stat()
    try:
        row_count = pq.read_metadata(path).num_rows
    except OSError:
        row_count = -1
    return {
        'family': path.parent.name,
        'file': path.name,
        'path': str(path),
        'rows': row_count,
        'size_bytes': stat.st_size,
        'modified_at': datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        'config_fingerprint': read_fingerprint_from_parquet(path) or '',
    }
