"""
FiniexTestingIDE - Run Tree Pruner
Classify the run tree into what may go and what must stay, then remove exactly that.

Separate from RunIndex on purpose: the index DESCRIBES runs, this touches the TREE and lets
the index follow afterwards. A prune that edited index rows without removing directories — or
the reverse — would break the one invariant #475 rests on.
"""

import shutil
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional

from python.configuration.app_config_manager import AppConfigManager
from python.framework.reporting.io.run_header_io import RUN_HEADER_ARTIFACT
from python.framework.reporting.store.run_index import RunIndex
from python.framework.types.api.report_types import RunInfo, RunReporting
from python.framework.types.config_types.file_logging_config_types import RunLogPaths
from python.framework.types.log_layout_types import IO_SUBDIR
from python.framework.types.run_prune_types import (
    PruneCandidate,
    PruneReport,
    PruneResult,
    PruneSelectors,
)

# The raw record behind a real-money release certificate. `FieldStudyCertificate` finds it by
# rglob under the live root, so a run holding one is evidence, not archive — no selector reaches
# it.
FIELD_STUDY_ARTIFACT = 'field_study.jsonl'

# A run's own substructure. These are never candidates in their own right; they go with the run
# directory that contains them.
_RUN_SUBDIRS = {IO_SUBDIR, 'scenario_logs', 'session_logs', 'diagnostics', 'events'}


def _dir_size(path: Path) -> int:
    """
    Bytes a directory occupies, including everything below it.

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


class RunTreePruner:
    """Decides what may be removed from the run tree, and removes exactly that."""

    def __init__(self, run_logs: Optional[RunLogPaths] = None,
                 run_index_path: Optional[Path] = None):
        """
        Args:
            run_logs: The run-type roots to walk; read from config when not given
            run_index_path: The index to read and rebuild; from config when not given.
                Injectable for the same reason the roots are — a caller pointed at an isolated
                tree must not ask the real index about runs that only exist there
        """
        file_logging = AppConfigManager().get_file_logging_config_object()
        self._roots = run_logs or file_logging.run_logs
        self._index = RunIndex(run_index_path or file_logging.run_index)

    def plan(self, selectors: PruneSelectors) -> PruneReport:
        """
        Classify the tree without touching it.

        Args:
            selectors: What the operator asked to be removed

        Returns:
            The full classification — what would go, what stays, and why
        """
        report = PruneReport()
        runs = self._index.list_runs()
        run_dirs = {r.run_id: self._index.run_dir(r.run_id) for r in runs}

        deletable = self._classify_runs(runs, run_dirs, selectors, report)

        if selectors.orphans:
            self._collect_orphans(set(run_dirs.values()), report)

        self._collect_emptied_sweeps(deletable, report)
        report.ledger_rows = self._count_ledger_rows()
        return report

    def apply(self, report: PruneReport) -> PruneResult:
        """
        Remove exactly what the report decided, then let the index follow.

        Takes the report rather than re-classifying: a dry run that showed one thing while the
        apply did another would defeat the reason the dry run is the default.

        Args:
            report: The classification produced by `plan()`

        Returns:
            What was removed, what failed, and the state of the rebuilt index
        """
        result = PruneResult()
        for candidate in report.all_deletions():
            try:
                shutil.rmtree(candidate.path)
                result.deleted.append(candidate.path)
            except OSError as e:
                # One unremovable directory does not abort the prune — the rest are still worth
                # removing, and the operator needs the whole list rather than the first failure.
                result.failed.append(f'{candidate.path}: {e}')

        result.indexed_after_rebuild = self._index.rebuild(self._roots)
        result.duplicate_ids = self._index.duplicate_ids()
        return result

    # =========================================================================
    # Classification
    # =========================================================================

    def _classify_runs(self, runs: List[RunInfo], run_dirs: Dict[str, Optional[Path]],
                       selectors: PruneSelectors, report: PruneReport) -> List[Path]:
        """
        Sort every indexed run into exactly one group.

        Args:
            runs: The index rows
            run_dirs: run_id → directory
            selectors: What the operator asked for
            report: Filled in place

        Returns:
            The directories this plan would delete — the input to the sweep-emptying check
        """
        keepers = self._keep_last_survivors(runs, selectors.keep_last)
        deletable: List[Path] = []

        for run in runs:
            run_dir = run_dirs.get(run.run_id)
            if run_dir is None or not run_dir.exists():
                # The row outlived its directory. There is nothing to delete, but the rebuild
                # will drop the row — so it is reported rather than passed over: a dry run that
                # showed an empty report while three rows were about to vanish would be lying
                # by omission.
                report.stale_rows.append(PruneCandidate(
                    path=run_dir or Path(run.run_id), size_bytes=0, run_id=run.run_id,
                    run_type=run.group, run_name=run.name))
                continue
            candidate = PruneCandidate(
                path=run_dir, size_bytes=_dir_size(run_dir), run_id=run.run_id,
                run_type=run.group, run_name=run.name)

            # The guard first, so nothing below can reach it: a run that crashed before
            # reporting is the only record of that failure.
            if run.reporting == RunReporting.EXPECTED and not run.artifacts:
                report.kept_incomplete.append(candidate)
                continue
            # Evidence behind a release gate — untouchable by every selector.
            if (run_dir / FIELD_STUDY_ARTIFACT).exists():
                report.kept_field_study.append(candidate)
                continue
            # Always-on: commissioned to produce nothing, and it produced nothing.
            if run.reporting == RunReporting.NONE and not run.artifacts:
                report.to_delete_uncommissioned.append(candidate)
                deletable.append(run_dir)
                continue
            if keepers is not None and run.run_id not in keepers:
                report.to_delete_redundant.append(candidate)
                deletable.append(run_dir)
                continue
            report.kept_complete.append(candidate)

        return deletable

    @staticmethod
    def _keep_last_survivors(runs: List[RunInfo], keep_last: Optional[int]) -> Optional[set]:
        """
        The run ids `--keep-last N` spares.

        Two units, because a sweep is not a run and must not be counted like one:

        - a standalone run belongs to the family `(group, run_name)` — the redundancy this
          removes comes from running the same scenario set or profile again
        - a sweep's combinations are NOT a family among themselves. The SWEEP is the unit: the N
          newest sweeps survive WHOLE, the rest go WHOLE. Counting combinations instead would
          keep 2 of 4 and leave a `ranked.csv` ranking runs that no longer exist — a half-pruned
          sweep is worse than an unpruned one

        Args:
            runs: The index rows
            keep_last: How many newest to keep per unit; None disables the selector

        Returns:
            The spared ids, or None when the selector is off
        """
        if keep_last is None:
            return None

        standalone: Dict[str, List[RunInfo]] = defaultdict(list)
        by_sweep: Dict[str, List[RunInfo]] = defaultdict(list)
        for run in runs:
            if run.parent_id:
                by_sweep[run.parent_id].append(run)
            else:
                standalone[f'{run.group}/{run.name}'].append(run)

        survivors = set()
        for members in standalone.values():
            # run_id is `<date>_<time>_<hash>` with a fixed-width prefix, so sorting the id
            # descending is sorting by time descending (#475).
            survivors.update(
                r.run_id for r in sorted(members, key=lambda r: r.run_id, reverse=True)[:keep_last])

        # The sweep ids themselves carry a timestamp prefix, so the same ordering applies one
        # level up. Every combination of a surviving sweep survives with it.
        for sweep_id in sorted(by_sweep, reverse=True)[:keep_last]:
            survivors.update(r.run_id for r in by_sweep[sweep_id])
        return survivors

    def _collect_orphans(self, known_dirs: set, report: PruneReport) -> None:
        """
        Directories in the tree that are not runs.

        Three things must NOT land here and each is excluded for its own reason: a run's own
        substructure (it goes with its run), a sweep directory (correctly header-less — a sweep
        is not a run), and any directory the index knows.

        Args:
            known_dirs: The directories the index lists
            report: Filled in place
        """
        for root in (Path(self._roots.simulation), Path(self._roots.live)):
            if not root.exists():
                continue
            for path in root.rglob('*'):
                if not path.is_dir() or path in known_dirs:
                    continue
                if path.name in _RUN_SUBDIRS or self._inside_run_dir(path, known_dirs):
                    continue
                if self._is_sweep_dir(path):
                    report.skipped_sweep_dirs.append(
                        PruneCandidate(path=path, size_bytes=_dir_size(path)))
                    continue
                if any(child.is_file() for child in path.iterdir()):
                    report.to_delete_orphans.append(
                        PruneCandidate(path=path, size_bytes=_dir_size(path)))

    @staticmethod
    def _inside_run_dir(path: Path, known_dirs: set) -> bool:
        """
        Whether a directory lives inside a known run.

        Args:
            path: The directory
            known_dirs: The directories the index lists

        Returns:
            True when one of its parents is an indexed run
        """
        return any(parent in known_dirs for parent in path.parents)

    def _is_sweep_dir(self, path: Path) -> bool:
        """
        Whether a directory is a sweep's own directory.

        Args:
            path: The directory

        Returns:
            True when it sits directly under the sweeps root
        """
        return path.parent == Path(self._roots.sweeps)

    def _collect_emptied_sweeps(self, deletable: List[Path], report: PruneReport) -> None:
        """
        Sweep directories this prune would leave without a single combination.

        They go with their combinations rather than staying behind: a sweep directory holds a
        `ranked.csv` and a `mount_build.log`, and both describe runs that would no longer exist.

        Args:
            deletable: The run directories this plan removes
            report: Filled in place
        """
        sweeps_root = Path(self._roots.sweeps)
        if not sweeps_root.exists():
            return

        going = set(deletable)
        for sweep_dir in sweeps_root.iterdir():
            if not sweep_dir.is_dir():
                continue
            combinations = [d for d in sweep_dir.rglob(RUN_HEADER_ARTIFACT)]
            if not combinations:
                continue
            if all(header.parent in going for header in combinations):
                report.emptied_sweep_dirs.append(
                    PruneCandidate(path=sweep_dir, size_bytes=_dir_size(sweep_dir)))

    @staticmethod
    def _count_ledger_rows() -> int:
        """
        How many cross-run ledger fragments exist.

        Reported by the prune so the operator sees, once, that the result history is NOT what is
        being deleted — index and ledger have opposite retention on purpose (#390).

        Returns:
            The fragment count, or 0 when the ledger directory does not exist
        """
        ledger_dir = Path(AppConfigManager().get_run_results_path())
        if not ledger_dir.exists():
            return 0
        return len(list(ledger_dir.glob('*.parquet')))
