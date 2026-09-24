"""
FiniexTestingIDE - Run Prune Types
What a prune was asked to do, what it decided, and what it actually did.
"""

from dataclasses import dataclass, field
from datetime import timedelta
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
    # Drop runs that started longer ago than this. None = do not select by age.
    #
    # It COMPOSES with keep_last rather than replacing it, and the composition is the
    # conservative one: a run goes only when EVERY active selector releases it. "Keep the
    # newest five" and "keep the last month" are two KEEP rules, so satisfying either one is
    # enough to stay. The other reading — one selector is enough to delete — would throw away
    # a run that is among the newest five of its family merely for being old, which is the
    # opposite of what an operator asking for both means.
    older_than: Optional[timedelta] = None
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

    This IS the product of a preview, and `RunTreePruner.apply()` consumes it rather than
    re-deriving the decision — so what the operator was shown is exactly what gets deleted.
    """
    to_delete_orphans: List[PruneCandidate] = field(default_factory=list)
    to_delete_redundant: List[PruneCandidate] = field(default_factory=list)
    to_delete_uncommissioned: List[PruneCandidate] = field(default_factory=list)
    # The kept groups, carried so the preview can SAY why a run stayed rather than leaving
    # the operator to infer it from an absence. One group per REASON, never one per run —
    # a group that mixed two reasons would answer neither.
    kept_incomplete: List[PruneCandidate] = field(default_factory=list)
    kept_field_study: List[PruneCandidate] = field(default_factory=list)
    kept_complete: List[PruneCandidate] = field(default_factory=list)
    # Spared by the age selector because they are younger than the window it named. Its own
    # group rather than folded into kept_complete: when an operator passes --older-than, this
    # count IS the answer to what they asked.
    kept_recent: List[PruneCandidate] = field(default_factory=list)
    # Spared because their start time is not recorded, so their age cannot be measured. An
    # age nobody can measure is not a reason to delete — and saying so is not the same
    # statement as "this run is recent", which is why it is not the group above.
    kept_undated: List[PruneCandidate] = field(default_factory=list)
    skipped_sweep_dirs: List[PruneCandidate] = field(default_factory=list)
    # Index rows whose directory is gone — the tree was cleared by hand, or a run was removed
    # outside this command. Nothing to delete, but the rebuild WILL drop them, so a preview that
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
    # Ledger rows whose run directory this prune removed, stamped so they stop claiming their
    # figures can be re-derived. The rows are NOT deleted — index and ledger keep opposite
    # retention on purpose (#390), and a result outlives the evidence behind it.
    ledger_rows_marked: int = 0
