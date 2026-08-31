"""
FiniexTestingIDE - Run Prune Types
What a prune was asked to do, what it decided, and what it actually did.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


@dataclass
class PruneSelectors:
    """
    What the operator asked to be removed.

    Every selector is opt-in except the always-on one, and none of them can reach a run the
    guard protects. Nothing here is a policy the framework decides for itself — a prune runs
    when it is called, with the selectors it was called with.
    """
    # Per (group, run_name) — and per parent_id for a sweep's combinations — keep the N newest
    # complete runs and drop the rest. None = do not select by redundancy.
    keep_last: Optional[int] = None
    # Directories in the tree that are not runs: no header, not in the index, not a sweep
    # directory, not a run's own substructure.
    orphans: bool = False


@dataclass
class PruneCandidate:
    """One directory a prune would remove, with what it takes to judge the decision."""
    path: Path
    size_bytes: int
    run_id: str = ''            # empty for an orphan — it has no identity
    run_type: str = ''
    run_name: str = ''


@dataclass
class PruneReport:
    """
    The classification, computed without touching anything.

    This IS the product of a dry run, and `RunTreePruner.apply()` consumes it rather than
    re-deriving the decision — so what the operator was shown is exactly what gets deleted.
    """
    to_delete_orphans: List[PruneCandidate] = field(default_factory=list)
    to_delete_redundant: List[PruneCandidate] = field(default_factory=list)
    to_delete_uncommissioned: List[PruneCandidate] = field(default_factory=list)
    # The four kept groups, carried so the dry run can SAY why a run stayed rather than
    # leaving the operator to infer it from an absence.
    kept_incomplete: List[PruneCandidate] = field(default_factory=list)
    kept_field_study: List[PruneCandidate] = field(default_factory=list)
    kept_complete: List[PruneCandidate] = field(default_factory=list)
    skipped_sweep_dirs: List[PruneCandidate] = field(default_factory=list)
    # Index rows whose directory is gone — the tree was cleared by hand, or a run was removed
    # outside this command. Nothing to delete, but the rebuild WILL drop them, so a dry run that
    # stayed silent about it would hide a change it is supposed to announce.
    stale_rows: List[PruneCandidate] = field(default_factory=list)
    # Sweep directories left empty by this prune's own deletions — removed with them, never
    # half: a sweep directory without its combinations holds a ranked.csv ranking nothing.
    emptied_sweep_dirs: List[PruneCandidate] = field(default_factory=list)
    ledger_rows: int = 0

    def all_deletions(self) -> List[PruneCandidate]:
        """
        Every directory this report would remove, in deletion order.

        Sweep directories come last: their combinations live inside them.

        Returns:
            The candidates, runs and orphans first, emptied sweep directories after
        """
        return (self.to_delete_orphans + self.to_delete_redundant
                + self.to_delete_uncommissioned + self.emptied_sweep_dirs)

    def total_bytes(self) -> int:
        """
        How much the deletions would free.

        Returns:
            Sum over every candidate this report would remove
        """
        return sum(c.size_bytes for c in self.all_deletions())


@dataclass
class PruneResult:
    """What actually happened — deletions that succeeded, and the ones that did not."""
    deleted: List[Path] = field(default_factory=list)
    # A directory that could not be removed does not abort the prune: the remaining ones are
    # still worth removing, and the operator needs the whole list rather than the first failure.
    failed: List[str] = field(default_factory=list)
    indexed_after_rebuild: int = 0
    duplicate_ids: List[str] = field(default_factory=list)
