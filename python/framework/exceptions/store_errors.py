"""
Store model errors (#486).

The catalog and its indexes fail loudly rather than quietly: a store that cannot be resolved
and an index that cannot be rebuilt both look exactly like an empty one from the outside, and
an empty answer is the wrong answer to give about data that exists.
"""

from pathlib import Path

from python.framework.exceptions.finiex_error import FiniexError


class StoreCatalogError(FiniexError, ValueError):
    """A store was asked for that the catalog does not carry."""


class StoreIndexSourceMissingError(FiniexError, RuntimeError):
    """
    An index was asked to rebuild without the source it derives from.

    Not a silent empty rebuild: writing an empty index would report "no entries" for a store
    that is full, and the caller would have no way to tell that from the truth.
    """


class StoreIndexReplacedError(FiniexError, FileNotFoundError):
    """
    An index file vanished between two steps of reading or replacing it.

    Every writer of this application holds the index's lock (#650), so this needs a file deleted by
    hand, a program that takes no lock, or a reader that could not take it — on a data home it may only
    read, before any writer created the lock file. Nothing is lost, because an index is derived. It is a
    FileNotFoundError, so whoever caught the bare error before catches this one.
    """

    def __init__(self, path: Path, step: str):
        self.path = path
        self.step = step
        super().__init__(
            f'The index {path} vanished while it was being {step}. Every writer of this application '
            f'holds the lock beside it, so it was deleted by hand, replaced by a program that takes no '
            f'lock, or read where this process could not take the lock. Nothing is lost: the index is '
            f'derived, and the next read rebuilds it.'
        )


class LedgerFragmentVanishedError(FiniexError, FileNotFoundError):
    """
    A ledger fragment the index was being rebuilt from stayed missing while it was read again.

    A fragment is replaced in place when a booking period is added, and nothing deletes one, so a
    fragment that does not come back was removed by hand or by a process outside this application.
    """

    def __init__(self, path: Path, reread_s: float):
        self.path = path
        super().__init__(
            f'The ledger fragment {path} was not there for {reread_s:.0f} s while the ledger index was '
            f'rebuilt from it. A fragment is replaced in place when a booking period closes and is never '
            f'deleted, so it was removed by hand or by another program. The run it records is missing '
            f'from the ledger until the file is back.'
        )


class LedgerRowUnreadableError(FiniexError, ValueError):
    """A ledger row carries a structured column that will not parse back."""

    def __init__(self, run_id: str, column: str, detail: str):
        self.run_id = run_id
        self.column = column
        super().__init__(
            f"Ledger row '{run_id}' has an unreadable '{column}' column: {detail}. "
            f"The whole ledger reads as one table, so this one row stops every reader — "
            f"the fragment is runs/ledger/*_{run_id}.parquet."
        )


class EntryMemberRefusedError(FiniexError, ValueError):
    """
    A store entry was asked for a file its declaration does not allow: a member its pipeline never
    writes, a name part missing, or a name part that would reach into another directory.
    """
