"""
Store model types (#486) — the vocabulary the store catalog is written in.

Pure data: the enums that classify a data store and the status row the catalog
renders. The descriptor itself is NOT here — it carries an index factory, i.e. a
runtime collaborator, and lives beside the registry that builds it (§6).
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Optional


# The discovery caches' directory below data/processed. Named ONCE: the three cache families
# each declared it, the store catalog declared it a fourth time, and four literals for one
# directory is how a rename becomes a hunt. No leading dot — a cache is not a hidden file, and
# the store model names what it holds rather than tucking it away (#486).
DISCOVERY_CACHE_DIRNAME = 'discovery_caches'


class StoreKind(StrEnum):
    """
    What a store IS. Every store is exactly one kind, and the kind decides its obligations.

    RECORD and CARRY_OVER are never merged: a restart mints a new run id and a new directory,
    so a carry-over written under a run id could only be found by the next session guessing
    which directory was its predecessor. Different key, different lifetime.
    """
    RECORD = 'record'          # what happened — keyed by the event, immutable
    CARRY_OVER = 'carry_over'  # what reaches the NEXT run — keyed by the bot, overwriting
    ARCHIVE = 'archive'        # the inputs — append-only
    DERIVED = 'derived'        # index, cache, compaction — deletable by definition
    SPECIAL = 'special'        # declared with a stated reason, managed by nothing


class RetrievalForm(StrEnum):
    """
    How a store is READ. The reader belongs to the form, never to the individual artifact.

    RANGE stays outside the abstraction on purpose: a tick frame is never routed through a
    generic layer — the catalog hands out the path and steps aside. That is what keeps the
    model off the hot path.
    """
    DOCUMENT = 'document'      # A — one entry by identity, decoded into a typed model
    SET = 'set'                # B — many rows, filtered by predicate
    RANGE = 'range'            # C — bulk columnar read over a window; path only
    NONE = 'none'              # a SPECIAL store, read by nothing generic


class StoreBackend(StrEnum):
    """
    Where a store's entries physically live.

    DISK is the only value today. RAM exists because the resident-data registries (#418 mount
    registry, #21 file cache) are the same construction with a different backend — the field is
    what lets them dock into this catalog instead of becoming a third independent registry.
    """
    DISK = 'disk'
    RAM = 'ram'


class StoreId(StrEnum):
    """
    The registered stores. An enum rather than a free string, so a missing registration is a
    type error instead of a silent gap.
    """
    RUNS = 'runs'
    RUN_LEDGER = 'run_ledger'
    CERTIFICATES = 'certificates'
    SESSION_STATE = 'session_state'
    COLD_START_STATE = 'cold_start_state'
    TICKS = 'ticks'
    BARS = 'bars'
    GENERATOR_PROFILES = 'generator_profiles'
    SIGNALS = 'signals'
    DISCOVERY_CACHES = 'discovery_caches'
    BROKER_RUNTIME = 'broker_runtime'
    RAW_INBOX = 'raw_inbox'
    FINISHED_ARCHIVE = 'finished_archive'
    GLOBAL_LOG = 'global_log'


@dataclass
class StoreStatus:
    """
    One rendered row of the catalog — what a store holds right now.

    Measured on demand (the operator's `catalog` command), never on a hot path: counting entries
    means listing a directory, which is the expensive operation this model exists to keep out of
    the runtime paths.

    Args:
        store_id: Which store this describes
        kind: Its classification
        root: Where it lives, as configured
        key: How one entry is addressed
        form: How it is read
        backend: Where its entries physically live
        index_name: The index file's name, or None when the store has none
        note: Why there is no index, or why the store is SPECIAL — empty otherwise
        entries: How many entries it holds, or None when that cannot be counted cheaply
        size_bytes: Total size on disk, or None when not applicable
        exists: Whether the root is present at all
        self_healing: Whether the store's own read path rebuilds a stale index before serving
            it — then `stale_reason` is a note, not a task
        stale_reason: WHY the index needs a rebuild, in one clause — None when it is valid or
            when the store carries no index of ours to judge. A bare flag sends the operator
            to rebuild the wrong thing: a cache rebuild and an index rebuild are different
            commands
    """
    store_id: StoreId
    kind: StoreKind
    root: str
    key: str
    form: RetrievalForm
    backend: StoreBackend
    index_name: Optional[str]
    note: str
    entries: Optional[int]
    size_bytes: Optional[int]
    exists: bool
    stale_reason: Optional[str] = None
    self_healing: bool = False
