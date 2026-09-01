"""
Store descriptor (#486) — one entry in the store catalog.

Lives here rather than in `framework/types/` because it carries an index FACTORY: a runtime
collaborator, not data. A types module cannot import the index implementations it would have to
name without the import turning circular (§6, same reason as `reporting/builders/run_unit.py`).
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from python.framework.store.abstract_store_index import AbstractStoreIndex
from python.framework.types.store_types import (
    RetrievalForm,
    StoreBackend,
    StoreId,
    StoreKind,
)


@dataclass(frozen=True)
class StoreDescriptor:
    """
    What one data store IS, and how it is reached.

    Args:
        store_id: Its registered identity
        kind: RECORD / CARRY_OVER / ARCHIVE / DERIVED / SPECIAL
        root: Where it lives, resolved from configuration
        key: How ONE entry is addressed — for the operator's eye, not parsed
        form: How it is read; RANGE means the catalog hands out a path and steps aside
        backend: Where its entries physically live. DISK today; RAM is what lets the resident
            registries (#418 mount registry, #21 file cache) dock here later instead of becoming
            a third independent registry
        entry_glob: Pattern identifying ONE entry below root, for counting a store that has no
            index of ours. None when entries cannot be counted that way
        index_path: The index file, or None when the store has none
        index_factory: Builds the index object — None when the store has no index, or when the
            index is one this model does not own (the three data-index managers, which converge
            under #175)
        derived_from: The store this one is BUILT FROM. Mandatory for every DERIVED store and
            None otherwise. It is the EDGE, not the rule: WHICH store is the source is catalog
            data, while WHICH FILE a single entry watches stays with the family that owns the
            resolution — the catalog cannot enumerate (broker, symbol) pairs without importing
            the very index it would then have to keep fresh
        self_healing: True when the store's OWN read path rebuilds a stale index before serving
            it, so staleness is a note rather than a task. The run-results ledger is one: every
            run appends a fragment, which makes the index stale by construction, and
            `RunResultsLedger.read()` refreshes it on the way past. Reporting that as "rebuild
            before trusting it" would be a permanent warning about nothing
        note: Why a store is SPECIAL, or why a managed store deliberately has no index. Empty
            when neither applies
    """
    store_id: StoreId
    kind: StoreKind
    root: Path
    key: str
    form: RetrievalForm
    backend: StoreBackend
    entry_glob: Optional[str] = None
    index_path: Optional[Path] = None
    index_factory: Optional[Callable[[], AbstractStoreIndex]] = None
    derived_from: Optional[StoreId] = None
    self_healing: bool = False
    note: str = ''

    def build_index(self) -> Optional[AbstractStoreIndex]:
        """
        The store's index object, when this model owns one.

        Returns:
            A live index, or None for a store whose index is foreign or absent
        """
        return self.index_factory() if self.index_factory else None
