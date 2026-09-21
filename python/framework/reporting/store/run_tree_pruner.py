"""
FiniexTestingIDE - Run Tree Pruner
Classify the run tree into what may go and what must stay, then remove exactly that.

Separate from RunIndex on purpose: the index DESCRIBES runs, this touches the TREE and lets
the index follow afterwards. A prune that edited index rows without removing directories — or
the reverse — would break the one invariant #475 rests on.
"""

import os
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Callable, Dict, List, Optional

from python.configuration.app_config_manager import AppConfigManager
from python.framework.reporting.io.run_header_io import RUN_HEADER_ARTIFACT
from python.framework.reporting.store.run_index import (
    RunIndex,
    dir_size,
    own_files_size,
)
from python.framework.reporting.store.run_ledger_index import LEDGER_INDEX_FILE
from python.framework.reporting.store.run_results_ledger import RunResultsLedger
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


class RunTreePruner:
    """Decides what may be removed from the run tree, and removes exactly that."""

    def __init__(self, run_logs: Optional[RunLogPaths] = None,
                 run_index_path: Optional[Path] = None,
                 run_ledger_path: Optional[Path] = None):
        """
        Args:
            run_logs: The run-type roots to walk; read from config when not given
            run_index_path: The index to read and rebuild; from config when not given.
                Injectable for the same reason the roots are — a caller pointed at an isolated
                tree must not ask the real index about runs that only exist there
            run_ledger_path: The ledger whose rows are stamped when their run directory goes;
                from config when not given. Injectable for exactly the reason above, and here
                it is sharper: this store is WRITTEN, so a test pointed at a throwaway tree
                would otherwise stamp the real books
        """
        app_config = AppConfigManager()
        file_logging = app_config.get_file_logging_config_object()
        self._roots = run_logs or file_logging.run_logs
        self._index = RunIndex(run_index_path or file_logging.run_index, self._roots)
        self._ledger_dir = Path(run_ledger_path or app_config.get_run_ledger_path())

    def size_figures_available(self) -> bool:
        """
        Whether the index can answer how much disk each run occupies.

        False right after the column was added and before the first rebuild: the rows are
        there, the figure is not. Asked so the report can say UNKNOWN instead of printing
        0.0 MB, which on a delete screen reads as "this run is empty".

        Returns:
            True when the index was built by the current logic
        """
        return self._index.is_current()

    def plan(self, selectors: PruneSelectors,
             progress: Optional[Callable[[int, int], None]] = None) -> PruneReport:
        """
        Classify the tree without touching it.

        Args:
            selectors: What the operator asked to be removed
            progress: Called as (done, total) while the runs are classified, so a caller can
                show that the pass is moving. Every step here touches the filesystem, which
                costs 65-616x on this tree (§42), and silence looks like a hang

        Returns:
            The full classification — what would go, what stays, and why
        """
        report = PruneReport()
        runs = self._index.list_runs()
        # ONE read, not one per run: `run_dir()` opens the whole index each time it is asked,
        # so the comprehension that used to call it per run re-read the same file 118 times.
        # Measured 2026-09-18: 0.88 s against 0.003 s for reading it once into a dict.
        stored = self._index.run_dirs_of(r.run_id for r in runs)
        run_dirs = {r.run_id: Path(d) if d else None for r, d in zip(runs, stored)}

        deletable = self._classify_runs(runs, run_dirs, selectors, report, progress)

        if selectors.orphans:
            self._collect_orphans(set(run_dirs.values()), report)

        self._collect_emptied_sweeps(deletable, report)
        report.ledger_rows = self._count_ledger_rows()
        return report

    def apply(self, report: PruneReport) -> PruneResult:
        """
        Remove exactly what the report decided, then let the index follow.

        Takes the report rather than re-classifying: a preview that showed one thing while the
        apply did another would defeat the reason the preview is the default.

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

        result.indexed_after_rebuild = self._index.rebuild()
        result.duplicate_ids = self._index.duplicate_ids()

        # The ledger keeps its rows and says their evidence is gone (#390: the two stores have
        # opposite retention). Marked AFTER the deletions and only for what actually went —
        # `result.deleted` rather than the report — so a directory that refused to be removed
        # does not get a row claiming it was. Orphans carry no run_id and contribute nothing.
        removed = {c.run_id for c in report.all_deletions()
                   if c.run_id and c.path in set(result.deleted)}
        result.ledger_rows_marked = RunResultsLedger(
            self._ledger_dir).mark_records_pruned(removed)
        return result

    # =========================================================================
    # Classification
    # =========================================================================

    def _classify_runs(self, runs: List[RunInfo], run_dirs: Dict[str, Optional[Path]],
                       selectors: PruneSelectors, report: PruneReport,
                       progress: Optional[Callable[[int, int], None]] = None) -> List[Path]:
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

        for done, run in enumerate(runs, 1):
            if progress is not None:
                progress(done, len(runs))
            run_dir = run_dirs.get(run.run_id)
            if run_dir is None or not run_dir.exists():
                # The row outlived its directory. There is nothing to delete, but the rebuild
                # will drop the row — so it is reported rather than passed over: a preview that
                # showed an empty report while three rows were about to vanish would be lying
                # by omission.
                report.stale_rows.append(PruneCandidate(
                    path=run_dir or Path(run.run_id), size_bytes=0, run_id=run.run_id,
                    run_type=run.group, run_name=run.name))
                continue
            # The size comes from the INDEX, where it was stamped once when this run
            # finished. Walking for it here measured 42 s over the whole tree, most of it
            # spent on runs that were then kept (#486 / the index\'s own `size_bytes` note).
            candidate = PruneCandidate(
                path=run_dir, size_bytes=run.size_bytes, run_id=run.run_id,
                run_type=run.group, run_name=run.name)

            # The guard first, so nothing below can reach it: a run that crashed before
            # reporting is the only record of that failure.
            if run.reporting == RunReporting.EXPECTED and not run.artifacts:
                report.kept_incomplete.append(candidate)
                continue
            # Evidence behind a release gate — untouchable by every selector. Asked of the
            # FILESYSTEM and never of the index, unlike the size above: this one is a GUARD,
            # and a guard that trusts a derived file deletes real evidence the day that file
            # is stale. One stat per run is what the guarantee costs.
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

        Two units, because a parent is not a run and must not be counted like one:

        - a standalone run belongs to the family `(group, run_name)` — the redundancy this
          removes comes from running the same scenario set or profile again
        - the children of one parent are NOT a family among themselves. The PARENT is the unit:
          the N newest parents survive WHOLE, the rest go WHOLE. Counting children instead would
          keep 2 of 4 and leave a `ranked.csv` ranking runs that no longer exist — a half-pruned
          sweep is worse than an unpruned one

        There are TWO kinds of parent since #497 and they are counted alike: a sweep, whose
        children are its combinations, and a DEPLOYMENT, whose children are the sessions of one
        live bot across its restarts. Both are an identity that groups runs without being one,
        so `--keep-last N` spares the N newest of each — whole.

        Args:
            runs: The index rows
            keep_last: How many newest to keep per unit; None disables the selector

        Returns:
            The spared ids, or None when the selector is off
        """
        if keep_last is None:
            return None

        standalone: Dict[str, List[RunInfo]] = defaultdict(list)
        by_parent: Dict[str, List[RunInfo]] = defaultdict(list)
        for run in runs:
            if run.parent_id:
                by_parent[run.parent_id].append(run)
            else:
                standalone[f'{run.group}/{run.name}'].append(run)

        survivors = set()
        for members in standalone.values():
            # run_id is `<date>_<time>_<hash>` with a fixed-width prefix, so sorting the id
            # descending is sorting by time descending (#475).
            survivors.update(
                r.run_id for r in sorted(members, key=lambda r: r.run_id, reverse=True)[:keep_last])

        # Both parent identities carry a timestamp prefix, so the same ordering applies one
        # level up. Every child of a surviving parent survives with it.
        for parent_id in sorted(by_parent, reverse=True)[:keep_last]:
            survivors.update(r.run_id for r in by_parent[parent_id])
        return survivors

    def _collect_orphans(self, known_dirs: set, report: PruneReport) -> None:
        """
        Directories in the tree that are not runs.

        Three things must NOT land here and each is excluded for its own reason: a run's own
        substructure (it goes with its run), a sweep directory (correctly header-less — a sweep
        is not a run), and any directory the index knows.

        Walks with `os.walk` and PRUNES at every known run, which is the whole cost of this
        pass: an orphan is by definition a directory that is not a run and not inside one, so
        descending into a run can only ever find things this method must reject. Measured
        2026-09-18 on a tree of 733 directories and 7409 files: `rglob('*')` over everything
        took 17.7 s of an 18.7 s prune, because it stats each of those files to ask whether it
        is a directory. `os.walk` also hands back the file names per directory, which answers
        "does this hold anything" without a second listing.

        Args:
            known_dirs: The directories the index lists
            report: Filled in place
        """
        for root in (Path(self._roots.simulation), Path(self._roots.live)):
            if not root.exists():
                continue
            for dirpath, dirnames, filenames in os.walk(root):
                path = Path(dirpath)
                # Cut the branches at the PARENT, before os.walk lists them. A run's own
                # substructure goes with the run, so a listing below one can produce no
                # answer this method is allowed to give — and a listing is the single most
                # expensive operation on this mount (§42: a directory walk costs 282x).
                # Filtering here rather than skipping after the fact saved one listing per
                # run: 2.79 s down to 0.9 s over 118 runs, measured 2026-09-18.
                dirnames[:] = [d for d in dirnames
                               if d not in _RUN_SUBDIRS and (path / d) not in known_dirs]
                if path == root or path in known_dirs:
                    continue
                if self._is_sweep_dir(path):
                    # Its OWN files only. A sweep's combinations are runs in their own right
                    # and are already counted in their group, so a recursive size here would
                    # report the same bytes twice — and it was the expensive half of this
                    # pass, because it walked every combination's whole tree.
                    report.skipped_sweep_dirs.append(
                        PruneCandidate(path=path, size_bytes=own_files_size(path)))
                    continue
                if filenames:
                    report.to_delete_orphans.append(
                        PruneCandidate(path=path, size_bytes=dir_size(path)))

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
                # Same reason as the skipped ones above: the combinations are already in
                # the delete groups with their own sizes.
                report.emptied_sweep_dirs.append(
                    PruneCandidate(path=sweep_dir, size_bytes=own_files_size(sweep_dir)))

    def _count_ledger_rows(self) -> int:
        """
        How many cross-run ledger fragments exist.

        Reported by the prune so the operator sees, once, that the result history is NOT what is
        being deleted — index and ledger have opposite retention on purpose (#390).

        Returns:
            The fragment count, or 0 when the ledger directory does not exist
        """
        ledger_dir = self._ledger_dir
        if not ledger_dir.exists():
            return 0
        # The index lives at the root of its own store (§44) and is not a fragment, so counting
        # it reported one result more than the ledger holds — on a screen whose whole purpose is
        # to say how much history is NOT being deleted.
        return len([f for f in ledger_dir.glob('*.parquet') if f.name != LEDGER_INDEX_FILE])
