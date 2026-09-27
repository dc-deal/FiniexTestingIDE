"""
FiniexTestingIDE - Config Directory (#554)

Every configuration file that can start a run — scenario sets and AutoTrader profiles — with what
each declares and how often it ran. One derivation that the console listing and the API both
render (§12), served from a cache so a request costs a walk and a `stat` per file, and a file is
read only when it changed.

**A read writes nothing but its own cache.** The listing it replaces froze every file it saw into
the run-config store (a RECORD), so a half-finished file being edited became a permanent
"version". Here the run-config store is not touched at all.

**The run figures are joined at serve time** from the run index (`config_snapshot` is the source
file name in both pipelines), never cached with the row: a run that starts must show up at once,
and the file did not change because it ran.
"""

import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

from python.configuration.app_config_manager import AppConfigManager
from python.framework.config_directory.config_directory_builder import (
    market_type_lookup,
    read_config_file,
    read_scenarios,
)
from python.framework.config_directory.config_directory_discovery import discover_config_files
from python.framework.config_directory.config_directory_index import ConfigDirectoryIndex
from python.framework.reporting.store.run_index import RunIndex
from python.framework.types.api.directory_types import (
    DirectoryDetailResponse,
    DirectoryListResponse,
    DirectoryRow,
)
from python.framework.types.config_directory_types import ConfigKind, ConfigReadStatus
from python.framework.types.log_layout_types import RUN_TYPE_LIVE, RUN_TYPE_SIMULATION

# How long a refreshed directory is served before the next request walks the roots again. The
# walk is the cost — a `stat` per entry across the bridged mount (§42), ~0.2 s on this tree — so
# a page that asks twice in a breath pays it once; `refresh` skips the wait.
FRESHNESS_S = 30.0

# Which run type a configuration kind starts — how a row finds its runs in the run index.
_RUN_TYPE_OF = {ConfigKind.SCENARIO_SET: RUN_TYPE_SIMULATION,
                ConfigKind.AUTOTRADER_PROFILE: RUN_TYPE_LIVE}

# The last refresh per cache file, stamped on the MONOTONIC clock (§9): (when, rows, paths).
_MEMO: Dict[str, Tuple[float, List[DirectoryRow], Dict[str, Path]]] = {}


def clear_config_directory_memo() -> None:
    """Forget every served directory, so the next request walks the roots again."""
    _MEMO.clear()


class ConfigDirectory:
    """
    The directory of every configuration file that can start a run.

    Args:
        app_config: Supplies the roots and the store paths
    """

    def __init__(self, app_config: Optional[AppConfigManager] = None):
        self._app_config = app_config or AppConfigManager()
        self._index = ConfigDirectoryIndex(Path(self._app_config.get_config_directory_path()))
        self._run_index = RunIndex(self._app_config.get_file_logging_config_object().run_index)

    def list_configs(self, refresh: bool = False) -> DirectoryListResponse:
        """
        Every configuration file, newest-changed first, with its run figures.

        Args:
            refresh: Walk the roots now instead of serving a directory younger than FRESHNESS_S

        Returns:
            The directory
        """
        rows, _ = self._rows(refresh)
        return DirectoryListResponse(rows=rows, count=len(rows))

    def detail(self, file: str) -> Optional[DirectoryDetailResponse]:
        """
        One configuration file: its row, its scenarios read fresh, and the runs started from it.

        Args:
            file: The file name, as the directory lists it

        Returns:
            The detail, or None when the directory lists no such file
        """
        rows, paths = self._rows(refresh=False)
        row = next((candidate for candidate in rows if candidate.file == file), None)
        if row is None:
            return None
        scenarios = []
        if row.kind == ConfigKind.SCENARIO_SET:
            scenarios = read_scenarios(paths[file], market_type_lookup())
        runs = self._runs_of(self._run_index.read(), row)
        return DirectoryDetailResponse(row=row, scenarios=scenarios,
                                       runs=list(runs['run_id']) if not runs.empty else [])

    def path_of(self, file: str) -> Optional[Path]:
        """
        Where a listed configuration file lives — for a caller on this machine, never served.

        Args:
            file: The file name, as the directory lists it

        Returns:
            Its path, or None when the directory lists no such file
        """
        _, paths = self._rows(refresh=False)
        return paths.get(file)

    def _rows(self, refresh: bool) -> Tuple[List[DirectoryRow], Dict[str, Path]]:
        """
        The served rows and where each file lives, from the memo when it is fresh enough.

        Args:
            refresh: Ignore the memo

        Returns:
            (rows with run figures, file name → path)
        """
        key = str(self._index.get_path())
        cached = _MEMO.get(key)
        if cached and not refresh and time.monotonic() - cached[0] < FRESHNESS_S:
            return cached[1], cached[2]
        rows, paths = self._refreshed_rows()
        _MEMO[key] = (time.monotonic(), rows, paths)
        return rows, paths

    def _refreshed_rows(self) -> Tuple[List[DirectoryRow], Dict[str, Path]]:
        """
        Walk the roots, read what changed, write the cache when anything did, join the runs.

        Returns:
            (rows with run figures, newest-changed first; file name → path)
        """
        candidates = discover_config_files(self._app_config)
        cached = self._cached_rows()
        market_type_of = market_type_lookup()

        entries = []
        changed = set(cached) != {candidate.path.as_posix() for candidate in candidates}
        for candidate in candidates:
            path = candidate.path.as_posix()
            known = cached.get(path)
            if known is not None and (known[0], known[1]) == (candidate.mtime, candidate.size):
                row = known[2]
            else:
                row = read_config_file(candidate, market_type_of)
                changed = True
            entries.append((candidate, row))
        if changed:
            self._index.write(pd.DataFrame(
                [{'path': candidate.path.as_posix(), 'source_mtime': candidate.mtime,
                  'source_size': candidate.size, 'status': row.status.value,
                  'row_json': row.model_dump_json()} for candidate, row in entries],
                columns=ConfigDirectoryIndex.COLUMNS))

        served = [(candidate, row) for candidate, row in entries
                  if row.status != ConfigReadStatus.NOT_A_CONFIG]
        runs = self._run_index.read()
        rows = [self._with_runs(row.model_copy(update={'shadowed': candidate.shadowed}), runs)
                for candidate, row in served]
        rows.sort(key=lambda row: row.modified_at, reverse=True)
        return rows, {candidate.path.name: candidate.path for candidate, _ in served}

    def _cached_rows(self) -> Dict[str, Tuple[float, int, DirectoryRow]]:
        """
        The cache's rows, when this version of the logic wrote them.

        Returns:
            path → (mtime, size, row); empty when the cache is absent or from other logic, so
            every file is read again
        """
        if not self._index.is_current():
            return {}
        return {entry.path: (float(entry.source_mtime), int(entry.source_size),
                             DirectoryRow.model_validate_json(entry.row_json))
                for entry in self._index.read().itertuples()}

    @staticmethod
    def _runs_of(runs: pd.DataFrame, row: DirectoryRow) -> pd.DataFrame:
        """
        The runs on record started from one configuration file, newest first.

        Args:
            runs: The run index
            row: The configuration's row

        Returns:
            The matching run-index rows
        """
        if runs.empty or row.kind is None:
            return runs.iloc[0:0]
        matching = runs[(runs['config_snapshot'] == row.file)
                        & (runs['run_type'] == _RUN_TYPE_OF[row.kind])]
        return matching.sort_values('start_time', ascending=False)

    def _with_runs(self, row: DirectoryRow, runs: pd.DataFrame) -> DirectoryRow:
        """
        A row with its run figures joined in.

        Args:
            row: The cached row
            runs: The run index

        Returns:
            The row with run_count, last_run_at and last_run_id
        """
        matching = self._runs_of(runs, row)
        if matching.empty:
            return row
        newest = matching.iloc[0]
        return row.model_copy(update={'run_count': len(matching),
                                      'last_run_at': str(newest['start_time']),
                                      'last_run_id': str(newest['run_id'])})
