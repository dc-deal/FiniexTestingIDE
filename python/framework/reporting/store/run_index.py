"""
FiniexTestingIDE - Run Index
The derived, compacted table the API reads instead of walking the run tree.
"""

from pathlib import Path
from typing import List, Optional

import pandas as pd

from python.framework.reporting.io.run_header_io import RUN_HEADER_ARTIFACT, read_run_header
from python.framework.reporting.io.trade_history_report_io import TRADE_HISTORY_ARTIFACT
from python.framework.types.api.report_types import RunHeader, RunInfo
from python.framework.types.config_types.file_logging_config_types import RunLogPaths
from python.framework.types.log_layout_types import IO_SUBDIR

# Fixed column order, so the file stays readable back across versions.
INDEX_COLUMNS: List[str] = [
    'run_id', 'start_time', 'run_type', 'run_name', 'parent_id', 'run_dir', 'has_reports',
    'app_version', 'git_commit', 'config_snapshot',
]


def _or_none(value) -> Optional[str]:
    """Parquet reads a missing cell as NaN; the model wants None."""
    return value if isinstance(value, str) and value else None


class RunIndex:
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

    def __init__(self, path: Path):
        """
        Args:
            path: The index file (file_logging.run_index)
        """
        self._path = Path(path)

    def _frame(self) -> pd.DataFrame:
        """The current table, or an empty one with the right columns."""
        if not self._path.exists():
            return pd.DataFrame(columns=INDEX_COLUMNS)
        return pd.read_parquet(self._path)

    def _write(self, frame: pd.DataFrame) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        frame[INDEX_COLUMNS].to_parquet(self._path, index=False)

    def append(self, header: RunHeader, run_dir: Path) -> None:
        """
        Record a run at its START. Replaces an existing row of the same id.

        Args:
            header: The run's identity, as written to its header.json
            run_dir: Where the run's artifacts live
        """
        frame = self._frame()
        frame = frame[frame['run_id'] != header.run_id]
        row = pd.DataFrame([{
            'run_id': header.run_id,
            'start_time': header.start_time.isoformat(),
            'run_type': header.run_type,
            'run_name': header.run_name,
            'parent_id': header.parent_id,
            'run_dir': str(run_dir),
            'has_reports': False,
            'app_version': header.app_version,
            'git_commit': header.git_commit,
            'config_snapshot': header.config_snapshot,
        }])
        self._write(pd.concat([frame, row], ignore_index=True))

    def mark_reports_written(self, run_id: str) -> None:
        """
        Flip a run's `has_reports` once its artifacts exist.

        Kept as an explicit update rather than an existence check at read time: checking would
        mean one stat call per row on every request, which is the cost this index removes.

        Args:
            run_id: The run whose reports were just persisted
        """
        frame = self._frame()
        if frame.empty or run_id not in set(frame['run_id']):
            return
        frame.loc[frame['run_id'] == run_id, 'has_reports'] = True
        self._write(frame)

    def list_runs(self) -> List[RunInfo]:
        """
        Every indexed run, newest first.

        Returns:
            One identity row per run
        """
        frame = self._frame()
        if frame.empty:
            return []
        frame = frame.sort_values('run_id', ascending=False)
        return [RunInfo(run_id=r.run_id, group=r.run_type, name=r.run_name,
                        has_reports=bool(r.has_reports), start_time=r.start_time,
                        parent_id=_or_none(r.parent_id), app_version=r.app_version or '',
                        git_commit=_or_none(r.git_commit),
                        config_snapshot=r.config_snapshot or '')
                for r in frame.itertuples()]

    def run_dir(self, run_id: str) -> Optional[Path]:
        """
        Where a run's artifacts live, without walking the tree.

        Args:
            run_id: The run's identity

        Returns:
            Its directory, or None when the index does not carry it
        """
        frame = self._frame()
        hit = frame[frame['run_id'] == run_id] if not frame.empty else frame
        return Path(hit.iloc[0]['run_dir']) if len(hit) else None

    def rebuild(self, roots: RunLogPaths) -> int:
        """
        Rebuild the whole index from the headers on disk.

        The repair path, and the reason the index may be treated as disposable. A run without a
        header predates this mechanism and is skipped — it cannot be identified, which is exactly
        the condition the header exists to end.

        Args:
            roots: The three category roots to scan

        Returns:
            How many runs were indexed
        """
        rows = []
        for root in (roots.autotrader, roots.single_runs, roots.sweeps):
            for header_path in Path(root).rglob(RUN_HEADER_ARTIFACT):
                run_dir = header_path.parent
                header = read_run_header(header_path)
                rows.append({
                    'run_id': header.run_id,
                    'start_time': header.start_time.isoformat(),
                    'run_type': header.run_type,
                    'run_name': header.run_name,
                    'parent_id': header.parent_id,
                    'run_dir': str(run_dir),
                    'has_reports': (run_dir / IO_SUBDIR / TRADE_HISTORY_ARTIFACT).exists(),
                    'app_version': header.app_version,
                    'git_commit': header.git_commit,
                    'config_snapshot': header.config_snapshot,
                })
        self._write(pd.DataFrame(rows, columns=INDEX_COLUMNS))
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
        frame = self._frame()
        if frame.empty:
            return []
        counts = frame['run_id'].value_counts()
        return sorted(counts[counts > 1].index.tolist())
