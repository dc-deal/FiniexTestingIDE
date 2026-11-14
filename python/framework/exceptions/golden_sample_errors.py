"""
Golden sample errors (#557).

Freezing refuses rather than writes something doubtful: a sample is the reference the aggregation
refactor is checked against, so a sample that does not reproduce what its runs recorded, or that
cannot say which code made it, would turn every later comparison into a question.
"""

from typing import List

from python.framework.exceptions.finiex_error import FiniexError
from python.framework.types.golden_sample_types import GoldenSampleDifference


class GoldenSampleError(FiniexError, RuntimeError):
    """The shared root of the golden sample errors."""


class GoldenSampleNoProductionError(GoldenSampleError):
    """An entry was asked to be frozen that has no verified production."""

    def __init__(self, entry_id: str):
        self.entry_id = entry_id
        super().__init__(
            f"'{entry_id}' has no verified production to freeze. Produce it first: "
            f"python python/cli/fixture_catalog_cli.py produce {entry_id}"
        )


class GoldenSampleDirtyProductionError(GoldenSampleError):
    """A production ran on code no commit names, and freezing it was not explicitly allowed."""

    def __init__(self, entry_id: str, run_ids: List[str]):
        self.entry_id = entry_id
        self.run_ids = run_ids
        super().__init__(
            f"'{entry_id}' was produced on an uncommitted tree ({len(run_ids)} run(s): "
            f"{', '.join(run_ids)}), so no commit names the code its numbers come from. Commit, "
            f"produce again and freeze — or pass --allow-dirty for a sample that is not committed."
        )


class GoldenSampleNotReproducedError(GoldenSampleError):
    """Today's code does not rebuild an aggregate the way the frozen runs recorded it."""

    def __init__(self, entry_id: str, differences: List[GoldenSampleDifference]):
        self.entry_id = entry_id
        self.differences = differences
        shown = '\n'.join(
            f'  {d.check} · {d.subject} · {d.path}: recorded {d.recorded!r}, rebuilt {d.rebuilt!r}'
            for d in differences[:20])
        more = f'\n  … and {len(differences) - 20} more' if len(differences) > 20 else ''
        super().__init__(
            f"'{entry_id}': {len(differences)} field(s) are not reproduced from the frozen "
            f"inputs, so nothing was written:\n{shown}{more}"
        )


class GoldenSampleMissingError(GoldenSampleError):
    """A golden sample was read that has not been frozen."""

    def __init__(self, entry_id: str, folder: str):
        self.entry_id = entry_id
        super().__init__(
            f"No golden sample for '{entry_id}' at {folder}. Freeze it: "
            f"python python/cli/fixture_catalog_cli.py freeze {entry_id}"
        )
