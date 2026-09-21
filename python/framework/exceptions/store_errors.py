"""
Store model errors (#486).

The catalog and its indexes fail loudly rather than quietly: a store that cannot be resolved
and an index that cannot be rebuilt both look exactly like an empty one from the outside, and
an empty answer is the wrong answer to give about data that exists.
"""

from python.framework.exceptions.finiex_error import FiniexError


class StoreCatalogError(FiniexError, ValueError):
    """A store was asked for that the catalog does not carry."""


class StoreIndexSourceMissingError(FiniexError, RuntimeError):
    """
    An index was asked to rebuild without the source it derives from.

    Not a silent empty rebuild: writing an empty index would report "no entries" for a store
    that is full, and the caller would have no way to tell that from the truth.
    """


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
