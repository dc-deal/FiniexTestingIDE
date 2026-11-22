"""
Store index base (#486) — the shared machinery every store index repeats.

An index is DERIVED, and that is the property the whole store model rests on: it may be
deleted or go stale without anything being lost, because `rebuild()` reconstructs it from the
store's own contents. The store is the truth; the index is the read path.

It is ONE file, never a file per entry. Measured on this project: reading 404 small parquet
files costs 3.29 s while the same rows as a single file cost 0.008 s — 420x, and 99.6 % of
it is the file OPEN rather than the work.
"""

import errno
import os
import sys
from abc import ABC, abstractmethod
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, List, Optional

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from python.framework.exceptions.file_lock_errors import FileLockHeldError
from python.framework.exceptions.store_errors import StoreIndexReplacedError
from python.framework.types.store_types import StoreId
from python.framework.utils.atomic_file_utils import sync_file, sync_folder
from python.framework.utils.file_lock import FileLock
from python.framework.utils.file_utils import create_directory

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

# Beside every index, at the root of its store so it dies with the store: `runs_index.lock` beside
# `runs_index.parquet`. Whoever writes the index holds it alone, whoever reads it shares it (#650):
# where a replace is a delete followed by a rename, as on the development container's bridged drive,
# a reader would otherwise land in the gap, and two writers share one temporary file everywhere.
INDEX_LOCK_SUFFIX = '.lock'

# How long a reader waits for a writer, and a writer for the readers and another writer. A writer holds
# an index for its write (milliseconds) or, inside `read_valid()`, for one rebuild that reads no other
# index — the ledger's took 3.5-6.4 s on the bridged drive. A rebuild of the run index takes about two
# minutes there (measured 2026-11-20: 214 runs in 127 s), which is why no rebuild holds the lock while
# it scans its sources.
INDEX_LOCK_WAIT_S = 120.0

# How long `read_valid()` tries to take a stale index alone before it derives the contents in memory:
# waiting longer buys the reader nothing its own derivation does not.
REBUILD_LOCK_WAIT_S = 5.0

# How long the AutoTrader's own index writes wait before giving up with a warning: inside the time a stop
# keeps free for its records (`autotrader.shutdown.reserve_for_records_s`). An index is derived, so a write
# given up loses nothing a rebuild cannot restore, and a session never ends over one.
SESSION_INDEX_LOCK_WAIT_S = 5.0

# The bound every index instance of this process starts with: INDEX_LOCK_WAIT_S unless the process
# declares its own. The API server does — its readers run in a pool of worker threads, and one that
# waits two minutes on a hung holder takes a thread every route needs, so it waits briefly and answers
# that the index is busy (#650).
_process_lock_wait_s = INDEX_LOCK_WAIT_S

# A data home this process may not write: its lock files can only be opened for reading, or not at all
_READ_ONLY_ERRNOS = (errno.EROFS, errno.EACCES, errno.EPERM)


def set_process_lock_wait_s(wait_s: float) -> None:
    """
    The bound every store index instance of this process starts with.

    Args:
        wait_s: The bound in seconds, for a process that must not wait INDEX_LOCK_WAIT_S
    """
    global _process_lock_wait_s
    _process_lock_wait_s = wait_s


def _holder_note() -> str:
    """
    What an exclusive holder of an index says about itself, for a process that waits on it.

    Returns:
        The process, its command and since when it holds the index, in UTC
    """
    command = ' '.join(sys.argv)[:200]
    since = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
    return f'pid {os.getpid()} · {command} · since {since}'


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
    # An index is derived — deletable and rebuilt from its store — so a crash of the machine costs
    # it a rebuild, and its writes are not forced to the disk. True only for an index that holds
    # what nothing else does (a store that declares `rebuild_loses`, #632)
    WRITES_DURABLY: bool = False

    def __init__(self, path: Path):
        self._path = Path(path)
        self._lock_wait_s = _process_lock_wait_s
        # The lock this instance holds right now, and whether alone: inside a hold, `read()` and
        # `write()` take no lock of their own
        self._held: Optional[FileLock] = None
        self._held_exclusive = False

    def get_path(self) -> Path:
        """
        Where this index is written.

        Returns:
            The index file path
        """
        return self._path

    def get_lock_path(self) -> Path:
        """
        The lock file beside the index.

        Returns:
            `<index name>.lock` in the index's own directory
        """
        return self._path.with_suffix(INDEX_LOCK_SUFFIX)

    def set_lock_wait_s(self, wait_s: float) -> None:
        """
        How long this instance waits for the index lock before giving up.

        Args:
            wait_s: The bound in seconds, where this caller must not wait as long as its process does
        """
        self._lock_wait_s = wait_s

    @contextmanager
    def updating(self, wait_s: Optional[float] = None) -> Iterator[None]:
        """
        Hold the index alone — for a write, or for a read and the write that follows it.

        Inside it `read()` and `write()` take no lock of their own: this instance holds it already, and
        a second `flock` of one process on one file would wait for itself. For the same reason an
        instance holding the index shared may not ask for it alone.

        Args:
            wait_s: How long to wait for the readers and another writer; this instance's bound when None

        Returns:
            A context; it raises FileLockHeldError when the wait runs out
        """
        if self._held is not None:
            if not self._held_exclusive:
                raise RuntimeError(f'{self._path} is held shared by this instance; release it before writing')
            yield
            return
        lock = FileLock(self.get_lock_path(), wait_s=self._lock_wait_s if wait_s is None else wait_s)
        lock.write_note(_holder_note())
        self._held, self._held_exclusive = lock, True
        try:
            yield
        finally:
            self._held = None
            # The note goes with its holder: left behind, it would name a process that is gone to a
            # writer refused later by readers, who write none
            lock.write_note('')
            lock.release()

    @contextmanager
    def _reading(self) -> Iterator[None]:
        """
        Share the index with other readers while reading it; nothing to take where this instance holds it.

        Returns:
            A context; it raises FileLockHeldError when a writer holds the index past this instance's bound
        """
        if self._held is not None:
            yield
            return
        lock = self._take_shared()
        if lock is None:
            yield
            return
        self._held, self._held_exclusive = lock, False
        try:
            yield
        finally:
            self._held = None
            lock.release()

    def _take_shared(self) -> Optional[FileLock]:
        """
        A shared hold on the lock file — opened for reading only on a data home this process may not write.

        Returns:
            The hold; None where the lock file can be neither created nor found, which on a read-only home
            means no writer has reached this index yet
        """
        path = self.get_lock_path()
        try:
            return FileLock(path, shared=True, wait_s=self._lock_wait_s)
        except OSError as error:
            if error.errno not in _READ_ONLY_ERRNOS:
                raise
        try:
            return FileLock(path, shared=True, wait_s=self._lock_wait_s, read_only=True)
        except FileNotFoundError:
            return None

    def _never_written(self) -> bool:
        """
        Whether no writer has reached this index yet — the one absence a reader may answer without the lock.

        Every writer creates the lock file before it writes the index, so an index that is missing BESIDE
        its lock file is one a writer is replacing right now: on a mount where a replace is a delete
        followed by a rename, a reader that answered from the missing file alone would serve an empty
        index in that gap, without a word. Only both missing means nothing to read, and then a reader
        creates no lock file either.

        Returns:
            True when neither the index nor its lock file is there
        """
        return not self._path.exists() and not self.get_lock_path().exists()

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
        if self._never_written():
            return pd.DataFrame(columns=self.COLUMNS)
        with self._reading():
            # A writer may have deleted it while this reader waited (the config directory's rebuild does)
            if not self._path.exists():
                return pd.DataFrame(columns=self.COLUMNS)
            try:
                return pd.read_parquet(self._path)
            except FileNotFoundError as error:
                raise StoreIndexReplacedError(self._path, 'read') from error

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
        table = pa.Table.from_pandas(frame[self.COLUMNS], preserve_index=False)
        metadata = dict(table.schema.metadata or {})
        version = self.LOGIC_VERSION if logic_version is None else logic_version
        metadata[LOGIC_VERSION_KEY] = str(version).encode()
        table = table.replace_schema_metadata(metadata)

        with self.updating():
            create_directory(self._path.parent, holds=f'the index {self._path.name}')
            # Not the shared atomic writer: its temporary name is unique per writer, and this one's
            # is shared on purpose — it is how a writer that does not hold the lock is caught below
            tmp_path = self._path.with_suffix(self._path.suffix + '.tmp')
            pq.write_table(table, tmp_path)
            if self.WRITES_DURABLY:
                sync_file(tmp_path)
            try:
                os.replace(tmp_path, self._path)
            except FileNotFoundError as error:
                # Every writer of this index uses the one temporary name, so a writer that does not
                # hold the lock and replaced first took this writer's file with it
                raise StoreIndexReplacedError(self._path, 'replaced') from error
            if self.WRITES_DURABLY:
                sync_folder(self._path.parent)

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
        with self.updating():
            mixed = self.exists() and self.stored_logic_version() != self.LOGIC_VERSION
            self.write(frame, logic_version=MIXED_LOGIC_VERSION if mixed else None)

    def stored_logic_version(self) -> Optional[int]:
        """
        The logic version the index file on disk was written with.

        Returns:
            The stored version, or None when absent or unreadable (an index written before the
            stamp existed reads as None, which `is_current` treats as out of date)
        """
        if self._never_written():
            return None
        with self._reading():
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

    def derive(self) -> Optional[pd.DataFrame]:
        """
        What a rebuild would write, computed without writing it.

        How `read_valid()` answers a reader that may not or cannot rebuild the index itself. An index
        whose rebuild cannot be separated from its write offers none.

        Returns:
            The contents a rebuild would write; None where this index offers no such derivation
        """
        return None

    def read_valid(self) -> pd.DataFrame:
        """
        The index contents, rebuilt first when they may not be served — derived in memory where the
        rebuild cannot be made.

        The check runs under a shared hold, which is given up before the index is asked for alone: a
        conversion `flock` cannot grant may leave the process holding nothing while it believes it
        holds the shared lock. Alone, the check runs again, because another process may have rebuilt
        the index while this one waited. A reader that does not get the index alone within
        REBUILD_LOCK_WAIT_S, or may not write where it lives, derives the contents itself: a reader
        never fails on the lock. An index that offers no derivation waits the full bound instead.

        Returns:
            The index contents
        """
        with self._reading():
            if self.is_valid():
                return self.read()
        try:
            with self.updating(wait_s=REBUILD_LOCK_WAIT_S):
                if not self.is_valid():
                    self.rebuild()
                return self.read()
        except (FileLockHeldError, OSError) as error:
            if isinstance(error, OSError) and error.errno not in _READ_ONLY_ERRNOS:
                raise
            derived = self.derive()
            if derived is not None:
                return derived
            if isinstance(error, OSError):
                raise
        # An index with no derivation of its own waits the full bound for the rebuild instead
        with self.updating():
            if not self.is_valid():
                self.rebuild()
            return self.read()

    @abstractmethod
    def rebuild(self) -> int:
        """
        Rebuild the whole index from the store's own contents.

        The repair path, and the reason an index may be treated as disposable.

        Returns:
            How many entries were indexed
        """
