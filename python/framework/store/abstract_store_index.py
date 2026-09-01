"""
Store index base (#486) — the shared machinery every store index repeats.

An index is DERIVED, and that is the property the whole store model rests on: it may be
deleted or go stale without anything being lost, because `rebuild()` reconstructs it from the
store's own contents. The store is the truth; the index is the read path.

It is ONE file, never a fragment per entry. Measured on this project: reading 404 small parquet
fragments costs 3.29 s while the same rows as a single file cost 0.008 s — 420x, and 99.6 % of
it is the file OPEN rather than the work.
"""

import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import List, Optional

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from python.framework.types.store_types import StoreId

# Arrow schema-metadata key carrying the producing code's version. Same mechanism the discovery
# caches already use for their source mtime and config fingerprint.
LOGIC_VERSION_KEY = b'store_index_logic_version'

# Stamped on a file that holds rows from MORE THAN ONE generation of the producing logic.
# It matches no subclass's LOGIC_VERSION, so `is_current()` reports False and a rebuild is
# demanded — which is the honest answer for content nobody can attribute to one version.
# A subclass must never declare LOGIC_VERSION = 0.
MIXED_LOGIC_VERSION = 0

# Every index this model owns is named after the store it describes: `<store_id>_index.parquet`.
# A rule rather than a preference — four indexes were named four ways (`index.parquet` twice,
# `.certificate_index.parquet`, and three legacy `.parquet_<x>_index.parquet`), so a file on disk
# did not say which store it belonged to and two of them had the same name in different folders.
INDEX_SUFFIX = '_index.parquet'


def store_index_filename(store_id: StoreId) -> str:
    """
    The index file name for a store.

    Args:
        store_id: The store the index describes

    Returns:
        `<store_id>_index.parquet`
    """
    return f'{store_id.value}{INDEX_SUFFIX}'


class AbstractStoreIndex(ABC):
    """
    One derived parquet index over one store.

    Subclasses declare COLUMNS (fixed order, so the file stays readable back across versions)
    and LOGIC_VERSION, and implement rebuild().

    LOGIC_VERSION closes a blind spot every pre-existing index family in this project shares:
    validity is keyed on the SOURCE mtime and never on the version of the code that produced
    the content. Change a scan function and the index is still newer than its sources, so a
    staleness check says "current" and the old content keeps being served — while the tests go
    green, because they exercise the code and not the file. Bump LOGIC_VERSION whenever the
    MEANING of a column changes, and `is_current()` turns that into a rebuild.

    Args:
        path: The index file this instance owns
    """

    COLUMNS: List[str] = []
    LOGIC_VERSION: int = 1

    def __init__(self, path: Path):
        self._path = Path(path)

    def get_path(self) -> Path:
        """
        Where this index is written.

        Returns:
            The index file path
        """
        return self._path

    def exists(self) -> bool:
        """
        Whether the index file is present.

        Returns:
            True when the file exists on disk
        """
        return self._path.exists()

    def read(self) -> pd.DataFrame:
        """
        The current table, or an empty one carrying the right columns.

        Returns:
            The index contents; empty with COLUMNS when the file is absent
        """
        if not self._path.exists():
            return pd.DataFrame(columns=self.COLUMNS)
        return pd.read_parquet(self._path)

    def write(self, frame: pd.DataFrame, logic_version: Optional[int] = None) -> None:
        """
        Replace the index atomically, stamped with a logic version.

        Atomic because a crash mid-write must not leave a half-written index: a truncated
        parquet is unreadable, and an unreadable index is indistinguishable from a missing one
        only until something tries to read it.

        Args:
            frame: The complete new contents; reordered to COLUMNS before writing
            logic_version: The version to stamp. Defaults to this class's, which is correct when
                every row was produced by the current logic — i.e. by `rebuild()`. An
                incremental writer must go through `write_incremental` instead
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        table = pa.Table.from_pandas(frame[self.COLUMNS], preserve_index=False)
        metadata = dict(table.schema.metadata or {})
        version = self.LOGIC_VERSION if logic_version is None else logic_version
        metadata[LOGIC_VERSION_KEY] = str(version).encode()
        table = table.replace_schema_metadata(metadata)

        tmp_path = self._path.with_suffix(self._path.suffix + '.tmp')
        pq.write_table(table, tmp_path)
        os.replace(tmp_path, self._path)

    def write_incremental(self, frame: pd.DataFrame) -> None:
        """
        Write an index that APPENDS to rows it did not produce.

        An incremental index reads its own file, adds a row and writes the whole thing back. A
        plain `write()` would then stamp the CURRENT logic version onto rows written by an older
        one — the file would claim to be current while most of its content is not, and the
        version check would be structurally disabled for exactly the store that appends most.

        So: stamp the current version only when the file on disk already carries it (or does not
        exist yet). Otherwise stamp MIXED, which no subclass matches, and the next reader is told
        to rebuild.

        Args:
            frame: The complete new contents
        """
        mixed = self.exists() and self.stored_logic_version() != self.LOGIC_VERSION
        self.write(frame, logic_version=MIXED_LOGIC_VERSION if mixed else None)

    def stored_logic_version(self) -> Optional[int]:
        """
        The logic version the index file on disk was written with.

        Returns:
            The stored version, or None when absent or unreadable (an index written before the
            stamp existed reads as None, which `is_current` treats as out of date)
        """
        if not self._path.exists():
            return None
        try:
            metadata = pq.read_schema(self._path).metadata or {}
            raw = metadata.get(LOGIC_VERSION_KEY)
            return int(raw.decode()) if raw else None
        except (OSError, ValueError, pa.ArrowException):
            return None

    def is_current(self) -> bool:
        """
        Whether the file on disk was produced by this version of the index logic.

        This answers only the CODE question. A store's own staleness — has the source changed
        since the index was built — stays with the subclass, because only it knows what its
        sources are.

        Returns:
            True when the file exists and carries this class's LOGIC_VERSION
        """
        return self.stored_logic_version() == self.LOGIC_VERSION

    def is_valid(self) -> bool:
        """
        Whether the index may be served without rebuilding.

        The default answers only the CODE question — the file exists and this logic wrote it. A
        subclass that can see its own sources overrides this to add the DATA question, and the
        two are genuinely different: a source can change without the code, and the code can
        change without the source. An index that checks only one of them is stale in the other
        direction and cannot tell.

        Returns:
            True when the index can be read as-is
        """
        return self.staleness_reason() is None

    def staleness_reason(self) -> Optional[str]:
        """
        WHY the index may not be served, in one operator-readable clause.

        "Stale" on its own sends the operator to rebuild the wrong thing: a cache rebuild and an
        index rebuild are different commands, and a bare flag does not say which is due. The
        default covers the code question; a subclass that can see its sources extends it.

        Returns:
            The reason, or None when the index is valid
        """
        if not self.exists():
            return 'never built'
        stored = self.stored_logic_version()
        if stored == MIXED_LOGIC_VERSION:
            return 'holds rows from more than one generation of the index logic'
        if stored is None:
            return 'written before the logic version was stamped'
        if stored != self.LOGIC_VERSION:
            return f'built by index logic v{stored}, current is v{self.LOGIC_VERSION}'
        return None

    @abstractmethod
    def rebuild(self) -> int:
        """
        Rebuild the whole index from the store's own contents.

        The repair path, and the reason an index may be treated as disposable.

        Returns:
            How many entries were indexed
        """
