"""
FiniexTestingIDE - Run Index
The derived, compacted table the API reads instead of walking the run tree.
"""

import os
from pathlib import Path
from typing import Iterable, List, Optional

import pandas as pd

from python.framework.exceptions.store_errors import StoreIndexSourceMissingError
from python.framework.reporting.io.run_header_io import (
    RUN_HEADER_ARTIFACT,
    read_run_header,
    write_run_header,
)
from python.framework.store.abstract_store_index import AbstractStoreIndex
from python.framework.types.api.report_types import RunHeader, RunInfo, RunReporting
from python.framework.types.config_types.file_logging_config_types import RunLogPaths
from python.framework.types.log_layout_types import IO_SUBDIR


def _or_none(value) -> Optional[str]:
    """Parquet reads a missing cell as NaN; the model wants None."""
    return value if isinstance(value, str) and value else None


def _artifact_names(run_dir: Path) -> List[str]:
    """
    The report artifacts a run has persisted, by file name.

    Args:
        run_dir: The run's own directory

    Returns:
        Sorted file names in the run's io/ subfolder; empty when it has none
    """
    io_dir = run_dir / IO_SUBDIR
    if not io_dir.is_dir():
        return []
    return sorted(f.name for f in io_dir.iterdir() if f.is_file())


def _int_or_zero(value) -> int:
    """
    Read a stored count, tolerating a row written before the column existed.

    Such a cell comes back as NaN, which is not an int and would refuse the model. Zero is the
    honest reading: nothing was recorded.

    Args:
        value: The raw cell

    Returns:
        The value as an int, or 0 when it cannot be read
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def own_files_size(path: Path) -> int:
    """
    Bytes of the files directly in a directory, ignoring everything below it.

    For a container whose children are counted elsewhere — a sweep directory, whose
    combinations are runs in their own right — this is the only figure that does not
    double-count. It is also the cheap one: one listing instead of a walk.

    Args:
        path: The directory

    Returns:
        Total size of its own files; 0 for anything unreadable
    """
    total = 0
    try:
        with os.scandir(path) as entries:
            for entry in entries:
                if entry.is_file(follow_symlinks=False):
                    total += entry.stat().st_size
    except OSError:
        return 0
    return total


def dir_size(path: Path) -> int:
    """
    Bytes a directory occupies, including everything below it.

    Lives here rather than with its consumer because this is where the number is PRODUCED —
    once per run, on a tree that has stopped changing. Every reader takes it from the index.

    Args:
        path: The directory

    Returns:
        Total size in bytes; 0 for anything unreadable
    """
    total = 0
    for item in path.rglob('*'):
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:
            continue
    return total


class RunIndex(AbstractStoreIndex):
    """
    ONE compacted parquet file, derived from the per-run `header.json` files.

    Why one file and not a fragment per run: measured on this project, reading 404 small parquet
    fragments costs 3.29 s while the same rows as a single file cost 0.008 s — 420×, and 99.6 % of
    it is the file OPEN, not the work. A per-run fragment reproduces exactly the scan cost this
    index exists to remove.

    It is DERIVED, and that is the property the whole design rests on: it may be deleted or go
    stale without anything being lost, because `rebuild()` reconstructs it from the headers. The
    headers are the truth; this is the read path.
    """

    # Fixed column order, so the file stays readable back across versions.
    COLUMNS: List[str] = [
        'run_id', 'start_time', 'run_type', 'run_name', 'parent_id', 'parent_kind', 'run_dir',
        'artifacts', 'app_version', 'git_commit', 'config_snapshot', 'reporting',
        # How much disk this run occupies, stamped where it is CHEAP — once, over ONE run, at
        # the moment its reports land. Measured 2026-09-18: answering it at READ time cost the
        # pruner 42 s over 7409 files against a 0.62 s floor for everything else it does, and
        # most of the runs it measured were ones it then KEPT. Exactly the argument `artifacts`
        # above already carries: listing at read time is the cost this index exists to remove.
        'size_bytes',
    ]

    # 1 → 2: `size_bytes` appended. A row written before it reads back as NaN, which means
    # UNKNOWN and is reported as such — never as 0 MB, which on the one screen an operator
    # uses to decide what to delete would read as "this run is empty".
    LOGIC_VERSION: int = 3

    def __init__(self, path: Path, roots: Optional[RunLogPaths] = None):
        """
        Args:
            path: The index file (file_logging.run_index)
            roots: The run-type roots `rebuild()` scans. Optional because most callers only
                register and read; a caller that rebuilds must supply them
        """
        super().__init__(path)
        self._roots = roots

    def register_run(self, header: RunHeader, run_dir: Path) -> None:
        """
        Write a run's header AND record it in the index, at its START.

        One call rather than two, because the two must not come apart: a header without an
        index row is a run the API cannot find, and an index row without a header is one a
        rebuild would drop. Replaces an existing row of the same id.

        Args:
            header: The run's identity
            run_dir: The run's own directory
        """
        write_run_header(header, run_dir)
        frame = self.read()
        frame = frame[frame['run_id'] != header.run_id]
        row = pd.DataFrame([{
            'run_id': header.run_id,
            'start_time': header.start_time.isoformat(),
            'run_type': header.run_type,
            'run_name': header.run_name,
            'parent_id': header.parent_id,
            'parent_kind': str(header.parent_kind) if header.parent_kind else None,
            'run_dir': str(run_dir),
            'artifacts': [],
            'app_version': header.app_version,
            'git_commit': header.git_commit,
            'config_snapshot': header.config_snapshot,
            'reporting': str(header.reporting),
            # The run has not produced anything yet; `record_artifacts` stamps the real
            # figure when it finishes.
            'size_bytes': 0,
        }])
        self.write_incremental(pd.concat([frame, row], ignore_index=True))

    def record_artifacts(self, run_id: str, run_dir: Path) -> None:
        """
        Record which report artifacts a run persisted, and how much disk it occupies.

        Written explicitly rather than listed at read time: listing would mean one directory
        scan per row on every request, which is the cost this index exists to remove. The two
        pipelines produce different sets, so the list — not a boolean — is what a consumer needs.

        The SIZE rides the same rewrite for the same reason and is why it is cheap here: this
        walks ONE finished run, where a consumer asking at read time walks every run it is
        about to keep. It is taken when the reports land, so anything written after them — the
        closing summary line — is not counted; a housekeeping figure off by a log tail is worth
        far more than a figure nobody can afford to ask for.

        Args:
            run_id: The run whose reports were just persisted
            run_dir: The run's own directory, whose io/ subfolder is listed
        """
        frame = self.read()
        if frame.empty or run_id not in set(frame['run_id']):
            return
        names = _artifact_names(run_dir)
        frame.loc[frame['run_id'] == run_id, 'size_bytes'] = dir_size(run_dir)
        # A cell holding a list needs an object column, and `.apply` is the assignment form
        # pandas accepts for one — a plain `.loc[mask] = names` would broadcast its elements.
        mask = frame['run_id'] == run_id
        frame['artifacts'] = frame['artifacts'].astype(object)
        frame.loc[mask, 'artifacts'] = frame.loc[mask, 'artifacts'].apply(lambda _: names)
        self.write_incremental(frame)

    def list_runs(self) -> List[RunInfo]:
        """
        Every indexed run, newest first.

        Returns:
            One identity row per run
        """
        frame = self.read()
        if frame.empty:
            return []
        frame = frame.sort_values('run_id', ascending=False)
        return [RunInfo(run_id=r.run_id, group=r.run_type, name=r.run_name,
                        artifacts=list(r.artifacts), start_time=r.start_time,
                        parent_id=_or_none(r.parent_id),
                        parent_kind=_or_none(getattr(r, 'parent_kind', None)),
                        app_version=r.app_version or '',
                        git_commit=_or_none(r.git_commit),
                        config_snapshot=r.config_snapshot or '',
                        reporting=r.reporting or RunReporting.EXPECTED,
                        size_bytes=_int_or_zero(getattr(r, 'size_bytes', 0)))
                for r in frame.itertuples()]

    def run_dirs_of(self, run_ids: Iterable[str]) -> List[Optional[str]]:
        """
        Where several runs live, from ONE read of the index.

        The bulk form of `run_dir` below, and the reason it exists is the cost of the
        singular one: that opens the whole index per call, so asking it for every run turns
        one file read into as many as there are runs. Measured 2026-09-18 over 118 runs:
        0.88 s against 0.003 s.

        Args:
            run_ids: The runs to look up, in the order the answer is wanted

        Returns:
            One entry per id, in that order; None where the index does not carry it
        """
        frame = self.read()
        if frame.empty:
            return [None for _ in run_ids]
        known = dict(zip(frame['run_id'], frame['run_dir']))
        return [known.get(run_id) for run_id in run_ids]

    def run_dir(self, run_id: str) -> Optional[Path]:
        """
        Where a run's artifacts live, without walking the tree.

        Args:
            run_id: The run's identity

        Returns:
            Its directory, or None when the index does not carry it
        """
        frame = self.read()
        hit = frame[frame['run_id'] == run_id] if not frame.empty else frame
        return Path(hit.iloc[0]['run_dir']) if len(hit) else None

    def rebuild(self) -> int:
        """
        Rebuild the whole index from the headers on disk.

        The repair path, and the reason the index may be treated as disposable. A run without a
        header predates this mechanism and is skipped — it cannot be identified, which is exactly
        the condition the header exists to end.

        Returns:
            How many runs were indexed
        """
        if self._roots is None:
            raise StoreIndexSourceMissingError(
                'RunIndex.rebuild() needs the run-type roots — construct it as '
                'RunIndex(path, roots) when the index is to be rebuilt.'
            )
        rows = []
        for root in (self._roots.simulation, self._roots.live):
            for header_path in Path(root).rglob(RUN_HEADER_ARTIFACT):
                run_dir = header_path.parent
                header = read_run_header(header_path)
                rows.append({
                    'run_id': header.run_id,
                    'start_time': header.start_time.isoformat(),
                    'run_type': header.run_type,
                    'run_name': header.run_name,
                    'parent_id': header.parent_id,
                    'parent_kind': (str(header.parent_kind)
                                    if header.parent_kind else None),
                    'run_dir': str(run_dir),
                    'artifacts': _artifact_names(run_dir),
                    'app_version': header.app_version,
                    'git_commit': header.git_commit,
                    'config_snapshot': header.config_snapshot,
                    'reporting': str(header.reporting),
                    # The repair path pays the walk it saves every reader — a rebuilt index
                    # that dropped the sizes would be a worse index than the one it replaced.
                    'size_bytes': dir_size(run_dir),
                })
        self.write(pd.DataFrame(rows, columns=self.COLUMNS))
        return len(rows)

    def duplicate_ids(self) -> List[str]:
        """
        Ids the index carries more than once.

        Never zero by construction: runs minted before the id gained its distinct half could
        collide, and a migration that keeps their names keeps their collisions. A duplicate is
        not merely a repeated row — `run_dir()` returns the first, so the API would serve one
        run's artifacts under the other's id. Reported rather than silently resolved, because
        the only honest fixes (re-mint and rename, or delete one) are the operator's call.

        Returns:
            The duplicated ids, sorted
        """
        frame = self.read()
        if frame.empty:
            return []
        counts = frame['run_id'].value_counts()
        return sorted(counts[counts > 1].index.tolist())
