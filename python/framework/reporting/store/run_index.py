"""
FiniexTestingIDE - Run Index
The derived, compacted table the API reads instead of walking the run tree.
"""

import json
import os
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

import pandas as pd

from python.framework.exceptions.store_errors import StoreIndexSourceMissingError
from python.framework.reporting.io.artifact_specs import STREAM_FILENAMES
from python.framework.reporting.io.run_header_io import (
    RUN_HEADER_ARTIFACT,
    read_run_header,
    write_run_header,
)
from python.framework.store.abstract_store_index import AbstractStoreIndex
from python.framework.types.api.report_types import DataWindow, RunHeader, RunInfo, RunReporting
from python.framework.types.config_types.file_logging_config_types import RunLogPaths
from python.framework.types.log_layout_types import IO_SUBDIR
from python.framework.types.run_purpose_types import RunPurpose


def _or_none(value) -> Optional[str]:
    """Parquet reads a missing cell as NaN; the model wants None."""
    return value if isinstance(value, str) and value else None


def _origin_columns(header: RunHeader) -> Dict[str, Any]:
    """
    The flat projection of a header's origin and code identity (#551), for both writers.

    One function for `register_run` and `rebuild`, so the repair path cannot project these
    differently from the append path — a rebuild that disagreed with the appends would make the
    index a second source of truth.

    None means UNKNOWN, never "no": a run written before the fields existed carries no origin and
    no code identity, and a run that was not commissioned to report captured no code identity.
    The two dirty flags answer different questions, and so treat an unreadable repository
    differently. `framework_dirty` asks whether THIS repository's tree differed from its commit —
    unknown whenever its state carries no commit: git could not run, could not read it, or found
    no repository around the checkout. `code_dirty` asks whether the code that ran can be
    reproduced from commits alone — any dirty, unversioned or unreadable repository says it
    cannot, which is `CodeIdentity.is_dirty()`.

    Args:
        header: The run's header

    Returns:
        The origin and dirty columns of one index row
    """
    origin = header.origin
    identity = header.code_identity
    framework = identity.framework if identity is not None else None
    framework_known = framework is not None and framework.commit is not None
    return {
        'origin_channel': str(origin.channel) if origin is not None else None,
        'origin_client': origin.client if origin is not None else None,
        'origin_principal': origin.principal if origin is not None else None,
        'host_id': origin.host if origin is not None else None,
        'framework_dirty': framework.dirty if framework_known else None,
        'code_dirty': identity.is_dirty() if identity is not None else None,
    }


def _io_file_names(run_dir: Path) -> List[str]:
    """
    Every file in a run's io/ subfolder, by name.

    Args:
        run_dir: The run's own directory

    Returns:
        Sorted file names; empty when the run has no io/ subfolder
    """
    io_dir = run_dir / IO_SUBDIR
    if not io_dir.is_dir():
        return []
    return sorted(f.name for f in io_dir.iterdir() if f.is_file())


def _artifact_names(run_dir: Path) -> List[str]:
    """
    The report artifacts a run has persisted, by file name.

    A stream is not one (#362): a live session writes its stream from its first order, and a
    session that died before its report would otherwise read as reported — "not completed"
    hangs on this list being empty.

    Args:
        run_dir: The run's own directory

    Returns:
        Sorted file names in the run's io/ subfolder, streams left out
    """
    return [name for name in _io_file_names(run_dir) if name not in STREAM_FILENAMES]


def _stream_names(run_dir: Path) -> List[str]:
    """
    The streams a run has written, by file name.

    Args:
        run_dir: The run's own directory

    Returns:
        Sorted stream file names in the run's io/ subfolder
    """
    return [name for name in _io_file_names(run_dir) if name in STREAM_FILENAMES]


def _kind_columns(header: RunHeader) -> Dict[str, Any]:
    """
    Which kind of run this is and the windows it covers, flattened from the header (contract 12).

    The windows travel as JSON text: a list of structures in a parquet column is the one shape an
    incremental append can widen differently from the file it lands in.

    Args:
        header: The run's header

    Returns:
        The columns, None where the header predates them
    """
    return {
        'ticks_from': str(header.ticks_from) if header.ticks_from else None,
        'orders_to': str(header.orders_to) if header.orders_to else None,
        'data_windows': (json.dumps([w.model_dump() for w in header.data_windows])
                         if header.data_windows is not None else None),
    }


def _purpose_columns(header: RunHeader, declared: Dict[str, RunPurpose]) -> Dict[str, Any]:
    """
    What the run is for, and the contract its reports were written under (#576), for both writers.

    A header written before `run_purpose` existed carries none. For such a run the index takes
    its configuration's CURRENT declaration, found by the file name the header recorded, where
    that configuration still exists; otherwise the cell stays empty, which means unknown. It is
    the one column derived rather than copied, and deliberately unlike `ticks_from`: that one a
    run RESOLVED from its configuration and its command line, which a file read today cannot
    reproduce, while a purpose is nothing but what the file declares. The header itself is never
    rewritten. `report_contract` has no such source and stays empty on an older header.

    Args:
        header: The run's header
        declared: Configuration file name → its declared purpose; empty on the append path,
            where every header already carries its own

    Returns:
        The two columns
    """
    purpose = header.run_purpose or declared.get(header.config_snapshot)
    return {
        'run_purpose': str(purpose) if purpose else None,
        'report_contract': header.report_contract,
    }


def _int_or_none(value: Any) -> Optional[int]:
    """
    Read a stored integer that may be missing — NaN on a row written before its column.

    Args:
        value: The raw cell

    Returns:
        The value as an int, or None when nothing was recorded
    """
    try:
        return None if pd.isna(value) else int(value)
    except (TypeError, ValueError):
        return None


def _data_windows(value) -> Optional[List[DataWindow]]:
    """
    The windows of an index row, parsed back.

    Args:
        value: The column's cell — JSON text, or empty on a row indexed before the column

    Returns:
        The windows, or None when the row carries none
    """
    if not isinstance(value, str) or not value:
        return None
    return [DataWindow(**window) for window in json.loads(value)]


def _stream_list(value: Any) -> List[str]:
    """
    A stored stream list as a list — absent on a row written before the column existed.

    Args:
        value: The cell, a list or array, or None / NaN

    Returns:
        The names; empty where nothing was recorded
    """
    if value is None or isinstance(value, float):
        return []
    return [str(name) for name in value]


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

    Why one file and not a file per run: measured on this project, reading 404 small parquet
    files costs 3.29 s while the same rows as a single file cost 0.008 s — 420×, and 99.6 % of
    it is the file OPEN, not the work. A per-run file reproduces exactly the scan cost this
    index exists to remove.

    It is DERIVED, and that is the property the whole design rests on: it may be deleted or go
    stale without anything being lost, because `rebuild()` reconstructs it from the headers. The
    headers are the truth; this is the read path.
    """

    # Fixed column order, so the file stays readable back across versions.
    COLUMNS: List[str] = [
        'run_id', 'start_time', 'run_type', 'run_name', 'parent_id', 'parent_kind', 'run_dir',
        'artifacts', 'app_version', 'git_commit', 'config_snapshot', 'config_id', 'reporting',
        # How much disk this run occupies, stamped where it is CHEAP — once, over ONE run, at
        # the moment its reports land. Measured 2026-09-18: answering it at READ time cost the
        # pruner 42 s over 7409 files against a 0.62 s floor for everything else it does, and
        # most of the runs it measured were ones it then KEPT. Exactly the argument `artifacts`
        # above already carries: listing at read time is the cost this index exists to remove.
        'size_bytes',
        # Where a run came from and whether its code can be reproduced from commits (#551),
        # flattened from the header so a selection by channel, principal, host or dirty state is a
        # column filter rather than a header read per run. The origin columns are served on
        # `RunInfo` since contract 26 (#582), `host_id` as `origin_host`; the dirty flags are not.
        'origin_channel', 'origin_principal', 'host_id', 'framework_dirty', 'code_dirty',
        # Which kind of run and the market windows it covers (contract 12), from the header.
        'ticks_from', 'orders_to', 'data_windows',
        # The streams the run wrote while it ran (#362) — kept apart from `artifacts`, which a
        # stream must not fill: see _artifact_names.
        'stream_files',
        # What the run is for and the contract its reports were written under (#576).
        'run_purpose', 'report_contract',
        # The client that started the run — the one origin field the list lacked (#582).
        'origin_client',
    ]

    # 1 → 2: `size_bytes` appended. A row written before it reads back as NaN, which means
    # UNKNOWN and is reported as such — never as 0 MB, which on the one screen an operator
    # uses to decide what to delete would read as "this run is empty".
    # 4 → 5: the origin and dirty columns appended (#551). A file written before them reports
    # itself out of date, and a rebuild fills them from the headers; left alone, its existing
    # rows read as unknown — which is also the truth for every run older than the fields.
    # 5 → 6: `ticks_from`, `orders_to` and `data_windows` appended (contract 12) — which kind of
    # run this is and the market windows it covers. Same shape as 4 → 5: a rebuild fills them,
    # and a header older than the fields reads as unknown.
    # 6 → 7 (#362): `stream_files` appended, and `artifacts` no longer lists a stream. A rebuild
    # fills it from io/; a row written before it reads as no streams, which is what those runs had.
    # 7 → 8 (#576): `run_purpose` and `report_contract` appended. A rebuild fills them; for a
    # header older than the fields the purpose comes from its configuration's current
    # declaration where one is found (_purpose_columns), and the contract stays unknown.
    # 8 → 9 (#582): `origin_client` appended, so the run list can serve the whole origin, and
    # `origin_person` renamed `origin_principal` — `person` is an account KIND (#551).
    LOGIC_VERSION: int = 9

    def __init__(self, path: Path, roots: Optional[RunLogPaths] = None,
                 declared_purposes: Optional[Callable[[], Dict[str, RunPurpose]]] = None):
        """
        Args:
            path: The index file (file_logging.run_index)
            roots: The run-type roots `rebuild()` scans. Optional because most callers only
                register and read; a caller that rebuilds must supply them
            declared_purposes: What every configuration declares its runs are for, by file
                name — asked once per `rebuild()` for the headers older than `run_purpose`.
                Handed in rather than imported: the configuration directory that answers it
                reads this index itself. None means no such run is given a purpose
        """
        super().__init__(path)
        self._roots = roots
        self._declared_purposes = declared_purposes

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
            'config_id': header.config_id,
            'reporting': str(header.reporting),
            # The run has not produced anything yet; `record_artifacts` stamps the real
            # figure when it finishes.
            'size_bytes': 0,
            **_origin_columns(header),
            **_kind_columns(header),
            'stream_files': [],
            **_purpose_columns(header, {}),
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
        self._stamp_streams(frame, mask, _stream_names(run_dir))
        self.write_incremental(frame)

    def record_streams(self, run_id: str, run_dir: Path) -> None:
        """
        Record which streams a run is writing — at the moment it starts writing them (#362).

        A stream is served while its run is still going, which is when it matters most: the run
        list has to name it before the report does. Only the streams are touched; the size and
        the artifacts stay unrecorded until the reports land, as `record_artifacts` explains.

        Args:
            run_id: The run whose stream was just opened
            run_dir: The run's own directory, whose io/ subfolder is listed
        """
        frame = self.read()
        if frame.empty or run_id not in set(frame['run_id']):
            return
        self._stamp_streams(frame, frame['run_id'] == run_id, _stream_names(run_dir))
        self.write_incremental(frame)

    @staticmethod
    def _stamp_streams(frame: pd.DataFrame, mask: pd.Series, names: List[str]) -> None:
        """
        Write one run's stream list into the frame, in place.

        Args:
            frame: The index frame
            mask: The run's row
            names: Its stream file names
        """
        if 'stream_files' not in frame.columns:
            frame['stream_files'] = [[] for _ in range(len(frame))]
        frame['stream_files'] = frame['stream_files'].astype(object)
        frame.loc[mask, 'stream_files'] = frame.loc[mask, 'stream_files'].apply(lambda _: names)

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
                        config_id=_or_none(getattr(r, 'config_id', None)) or '',
                        reporting=r.reporting or RunReporting.EXPECTED,
                        size_bytes=_int_or_zero(getattr(r, 'size_bytes', 0)),
                        ticks_from=_or_none(getattr(r, 'ticks_from', None)),
                        orders_to=_or_none(getattr(r, 'orders_to', None)),
                        data_windows=_data_windows(getattr(r, 'data_windows', None)),
                        stream_files=list(_stream_list(getattr(r, 'stream_files', None))),
                        run_purpose=_or_none(getattr(r, 'run_purpose', None)),
                        report_contract=_int_or_none(getattr(r, 'report_contract', None)),
                        origin_channel=_or_none(getattr(r, 'origin_channel', None)),
                        origin_client=_or_none(getattr(r, 'origin_client', None)),
                        origin_principal=_or_none(getattr(r, 'origin_principal', None)),
                        origin_host=_or_none(getattr(r, 'host_id', None)))
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
        declared = self._declared_purposes() if self._declared_purposes is not None else {}
        rows = []
        for root in (self._roots.simulation, self._roots.autotrader):
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
                    'config_id': header.config_id,
                    'reporting': str(header.reporting),
                    # The repair path pays the walk it saves every reader — a rebuilt index
                    # that dropped the sizes would be a worse index than the one it replaced.
                    'size_bytes': dir_size(run_dir),
                    **_origin_columns(header),
                    **_kind_columns(header),
                    'stream_files': _stream_names(run_dir),
                    **_purpose_columns(header, declared),
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
