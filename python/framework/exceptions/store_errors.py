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

    Another process replaced the same index at that moment. On a mount where a replace is a delete
    followed by a rename, as on the development container's bridged drive, a reader or a second
    writer can land in the gap between the two. Nothing is lost, because an index is derived. It is
    a FileNotFoundError, so whoever caught the bare error before catches this one (#650).
    """

    def __init__(self, path: Path, step: str):
        self.path = path
        self.step = step
        super().__init__(
            f'The index {path} vanished while it was being {step} - most likely another process '
            f'(the API server, a run, a CLI) is rebuilding it at this moment. Nothing is lost: the '
            f'index is derived, and the next read finds it rebuilt. Two processes rebuilding one '
            f'index at once is #650.'
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
