"""
Store model types (#486) — the vocabulary the store catalog is written in.

Pure data: the enums that classify a data store and the status row the catalog
renders. The descriptor itself is NOT here — it carries an index factory, i.e. a
runtime collaborator, and lives beside the registry that builds it (§6).
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Dict, Optional

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

    def writes_durably(self) -> Optional[bool]:
        """
        Whether a file of a store of this kind is forced to the disk before it replaces its
        target, so a crash of the machine cannot leave it empty (#632).

        A RECORD is what happened and a CARRY-OVER what the next run starts from — neither can be
        rebuilt — and an ARCHIVE holds the inputs. A DERIVED store is rebuilt from its source and
        deletable by definition, so the cost of a sync buys it nothing. A SPECIAL store answers
        for itself.

        Returns:
            True or False; None for SPECIAL, whose stores each state it
            (`SPECIAL_STORE_DURABILITY`)
        """
        if self is StoreKind.SPECIAL:
            return None
        return self is not StoreKind.DERIVED


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
    SWEEPS = 'sweeps'
    DIVERGENCE = 'divergence'
    RUN_CONFIGS = 'run_configs'
    RUN_PATCHES = 'run_patches'
    RUN_LEDGER = 'run_ledger'
    FIXTURE_PRODUCTIONS = 'fixture_productions'
    CERTIFICATES = 'certificates'
    GOLDEN_SAMPLES = 'golden_samples'
    SESSION_STATE = 'session_state'
    COLD_START_STATE = 'cold_start_state'
    TICKS = 'ticks'
    BARS = 'bars'
    GENERATOR_PROFILES = 'generator_profiles'
    SIGNALS = 'signals'
    DISCOVERY_CACHES = 'discovery_caches'
    BROKER_RUNTIME = 'broker_runtime'
    CONFIG_DIRECTORY = 'config_directory'
    RAW_INBOX = 'raw_inbox'
    REFUSED_FILES = 'refused_files'
    FINISHED_ARCHIVE = 'finished_archive'
    EXTERNAL_HISTORY = 'external_history'
    GLOBAL_LOG = 'global_log'
    IMPORT_LOGS = 'import_logs'
    IMPORT_TIMINGS = 'import_timings'
    HOST_IDENTITY = 'host_identity'
    ACTIVITIES = 'activities'

    def get_kind(self) -> StoreKind:
        """
        What this store is (`STORE_KINDS`).

        Returns:
            Its kind
        """
        return STORE_KINDS[self]

    def writes_durably(self) -> bool:
        """
        Whether this store's files are forced to the disk before they replace their target (#632)
        — its kind's answer, or for a SPECIAL store its own.

        Returns:
            True when a crash of the machine must not take a file of this store
        """
        answer = STORE_KINDS[self].writes_durably()
        return answer if answer is not None else SPECIAL_STORE_DURABILITY[self]


# What each store IS — the one place a store's kind is stated. Here, in a module that imports
# nothing of the project, so the low-level writer can ask a store how it writes without the
# store registry and its indexes (#632); the registry's descriptors read their kind from it.
STORE_KINDS: Dict[StoreId, StoreKind] = {
    StoreId.RUNS: StoreKind.RECORD,
    StoreId.SWEEPS: StoreKind.RECORD,
    StoreId.DIVERGENCE: StoreKind.RECORD,
    StoreId.RUN_CONFIGS: StoreKind.RECORD,
    StoreId.RUN_PATCHES: StoreKind.RECORD,
    StoreId.RUN_LEDGER: StoreKind.RECORD,
    StoreId.FIXTURE_PRODUCTIONS: StoreKind.RECORD,
    StoreId.CERTIFICATES: StoreKind.RECORD,
    StoreId.GOLDEN_SAMPLES: StoreKind.RECORD,
    StoreId.IMPORT_LOGS: StoreKind.RECORD,
    StoreId.SESSION_STATE: StoreKind.CARRY_OVER,
    StoreId.COLD_START_STATE: StoreKind.CARRY_OVER,
    StoreId.TICKS: StoreKind.ARCHIVE,
    StoreId.SIGNALS: StoreKind.ARCHIVE,
    StoreId.FINISHED_ARCHIVE: StoreKind.ARCHIVE,
    StoreId.EXTERNAL_HISTORY: StoreKind.ARCHIVE,
    StoreId.BARS: StoreKind.DERIVED,
    StoreId.GENERATOR_PROFILES: StoreKind.DERIVED,
    StoreId.DISCOVERY_CACHES: StoreKind.DERIVED,
    StoreId.BROKER_RUNTIME: StoreKind.DERIVED,
    StoreId.CONFIG_DIRECTORY: StoreKind.DERIVED,
    StoreId.IMPORT_TIMINGS: StoreKind.DERIVED,
    StoreId.RAW_INBOX: StoreKind.SPECIAL,
    StoreId.REFUSED_FILES: StoreKind.SPECIAL,
    StoreId.GLOBAL_LOG: StoreKind.SPECIAL,
    StoreId.HOST_IDENTITY: StoreKind.SPECIAL,
    StoreId.ACTIVITIES: StoreKind.SPECIAL,
}

# How each SPECIAL store writes, with its reason — the kind cannot answer for these (#632).
SPECIAL_STORE_DURABILITY: Dict[StoreId, bool] = {
    # A conveyor: a file rests there only until the import moves it on, and what it came from — a
    # venue's published history, a package its service keeps until the receipt — still exists
    StoreId.RAW_INBOX: False,
    # Files the import refused, moved there with their reason; the import is what they are for
    StoreId.REFUSED_FILES: False,
    # An append stream with no identity; nothing renames a file into it
    StoreId.GLOBAL_LOG: False,
    # Minted once and never rewritten: an identity lost to a crash of the machine is a new
    # installation, and every run before it names a host nobody can match
    StoreId.HOST_IDENTITY: True,
    # An entry lives as long as its activity's kernel lock — a crash of the machine ends both
    StoreId.ACTIVITIES: False,
}


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
        purpose: What the store is for, in one line
        doc: The document that explains it
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
        rebuild_loses: What a rebuild of the index cannot restore — empty when it restores
            everything
        entry_members: How many files one entry may hold, as the store declares them — 0 when an
            entry is a single file
    """
    store_id: StoreId
    kind: StoreKind
    purpose: str
    doc: str
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
    rebuild_loses: str = ''
    entry_members: int = 0
