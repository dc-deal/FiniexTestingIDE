"""
Store catalog (#486) — the one place that says which data stores exist.

Before this, the application wrote to eleven places and named none of them together: six had no
read path at all and could only be found by walking a directory, which is the most expensive
operation on this project's development mount. The catalog is not a new storage layer — the
bytes stay exactly where they are, in the shape each domain needs. What it adds is the answer to
"what exists, where, of what kind", and the obligation that a new store must be declared to be
reachable at all (CLAUDE.md §44).

It resolves its roots on construction, so a caller inside an isolated config environment gets
that environment's tree. Instantiate it where it is needed, like the config managers (§28).
"""

from typing import List, Optional

import pandas as pd

from python.framework.exceptions.store_errors import StoreCatalogError
from python.framework.store.store_descriptor import StoreDescriptor
from python.framework.store.store_registrations import build_registrations
from python.framework.types.store_types import StoreId, StoreStatus


class StoreCatalog:
    """The registered data stores, with their roots resolved against the current configuration."""

    def __init__(self):
        self._stores = build_registrations()

    def all(self) -> List[StoreDescriptor]:
        """
        Every registered store, in registration order.

        Returns:
            All descriptors
        """
        return list(self._stores.values())

    def get(self, store_id: StoreId) -> StoreDescriptor:
        """
        One store's descriptor.

        Args:
            store_id: Which store

        Returns:
            Its descriptor
        """
        if store_id not in self._stores:
            raise StoreCatalogError(
                f'No store registered under {store_id!r}. Every persistent write path is '
                f'declared in store_registrations.py — add it there (CLAUDE.md §44).'
            )
        return self._stores[store_id]

    def rebuild(self, store_id: StoreId) -> int:
        """
        Rebuild one store's index from the store's own contents.

        Args:
            store_id: Which store

        Returns:
            How many entries were indexed
        """
        descriptor = self.get(store_id)
        index = descriptor.build_index()
        if index is None:
            raise StoreCatalogError(
                f'Store {store_id!r} has no index this model owns — nothing to rebuild here. '
                f'{descriptor.note or "It carries no index."}'
            )
        return index.rebuild()

    def status(self, with_sizes: bool = False) -> List[StoreStatus]:
        """
        What every store holds right now — the operator's overview.

        Sizes are opt-in because measuring them means stat-ing every file in the store, and the
        tick archive alone is hundreds of files. Counting is cheap wherever an index exists: one
        file read instead of a directory walk, which is exactly what the indexes are for.

        Args:
            with_sizes: Also sum the bytes on disk (a full walk per store)

        Returns:
            One row per registered store
        """
        rows: List[StoreStatus] = []
        for descriptor in self.all():
            entries = self._count(descriptor)
            index = descriptor.build_index()
            rows.append(StoreStatus(
                store_id=descriptor.store_id,
                kind=descriptor.kind,
                root=str(descriptor.root),
                key=descriptor.key,
                form=descriptor.form,
                backend=descriptor.backend,
                index_name=descriptor.index_path.name if descriptor.index_path else None,
                note=descriptor.note,
                entries=entries,
                size_bytes=self._size(descriptor) if with_sizes else None,
                exists=descriptor.root.exists(),
                stale_reason=index.staleness_reason() if index is not None else None,
                self_healing=descriptor.self_healing,
            ))
        return rows

    def _count(self, descriptor: StoreDescriptor) -> Optional[int]:
        """
        How many entries a store holds, by the cheapest route available.

        Args:
            descriptor: The store to count

        Returns:
            The entry count, or None when the store cannot be counted (a SPECIAL one, or a
            missing root)
        """
        if not descriptor.root.exists():
            return None

        index = descriptor.build_index()
        if index is not None and index.exists():
            return len(index.read())

        # A foreign index (the three data-index managers) is still one file rather than a walk.
        if descriptor.index_path is not None and descriptor.index_path.exists():
            return len(pd.read_parquet(descriptor.index_path))

        if descriptor.entry_glob is None:
            return None
        return sum(1 for _ in self._entries(descriptor))

    def _size(self, descriptor: StoreDescriptor) -> Optional[int]:
        """
        Total bytes a store occupies.

        Args:
            descriptor: The store to measure

        Returns:
            Sum of its entry sizes, or None when it has no countable entries
        """
        if not descriptor.root.exists():
            return None
        if descriptor.root.is_file():
            return descriptor.root.stat().st_size
        if descriptor.entry_glob is None:
            return None
        return sum(path.stat().st_size for path in self._entries(descriptor))

    @staticmethod
    def _entries(descriptor: StoreDescriptor):
        """
        The store's entry files, with its own index excluded BY NAME.

        By name and not by a leading dot: the dot used to carry that meaning implicitly, and it
        stopped carrying it the moment the naming rule dropped it. An exclusion has to say what
        it excludes.

        Args:
            descriptor: The store to list

        Returns:
            Iterator over the entry paths
        """
        index_name = descriptor.index_path.name if descriptor.index_path else None
        return (path for path in descriptor.root.glob(descriptor.entry_glob)
                if path.is_file() and path.name != index_name)
