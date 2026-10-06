"""
FiniexTestingIDE - Run Config Types (#538)

What identifies the configuration a run was started from.

A scenario set and an AutoTrader profile are files at a path, and a path is not an identity: two
runs cannot be said to have used the same configuration, an edited file leaves no trace that it
changed, and the backtest half of a parity measurement cannot name its own strategy identity at
all. These types are what a run config becomes once it is a store entry instead of a file.

**Three hashes, because a change means three different things.** The content id says the BYTES
differ; `param_hash` says what the algo DECIDES differs; `scope_hash` says WHICH DATA is run
differs. Renaming a scenario moves the first and neither of the others — which is exactly the
distinction a reader needs and no single hash can make.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import List, Optional


class RunConfigKind(StrEnum):
    """
    What a registered document is.

    The two SOURCE kinds are kept apart for the same reason the two pipelines are kept apart
    everywhere else: a scenario set cascades and holds many scenarios, a profile is flat and holds
    one symbol. They share a store because they answer the same question — what was this run
    configured with — and nothing beyond that.

    The two RENDERED kinds (#547) answer the question a source cannot: what the run actually RAN
    with. A source file is the input to a cascade; the rendered profile is its result, with the
    `app_config` layer merged in and every schema default filled, and the broker configuration is
    what the venue's cache, the seed's fees and the detected fee tier added up to at session start.
    Neither has a file of its own to point at, so both are frozen from content. They are records of
    what ran, never schemas: a later version of an algo may rename or drop a parameter, and an old
    rendered document stays exactly as true as it was — it is never fed back into a factory.
    """
    SCENARIO_SET = 'scenario_set'
    AUTOTRADER_PROFILE = 'autotrader_profile'
    AUTOTRADER_RENDERED = 'autotrader_rendered'
    BROKER_CONFIG = 'broker_config'

    def has_source_file(self) -> bool:
        """
        Whether documents of this kind are registered from a file a caller can name.

        Returns:
            True for the two source kinds; False for the documents frozen from content
        """
        return self in (RunConfigKind.SCENARIO_SET, RunConfigKind.AUTOTRADER_PROFILE)


@dataclass
class RunConfigEntry:
    """
    One registered configuration, as the index holds it.

    Args:
        config_id: SHA256 over the NORMALISED content — the identity. Normalised rather than raw,
            so a reformatted file stays the same configuration, and computed by the same
            `generate_config_fingerprint` that `param_hash` already uses
        kind: Which pipeline it starts
        frozen_file: The store's own copy, named after the id. The store owns these bytes: a
            source may live in `user_algos/`, which is a separate repository
        source_name: The file name a caller asks for — the key the resolver looks up
        source_path: Where that file was last seen. A hint, not the identity: the frozen copy is
            what a run is reproducible from
        source_mtime: Its modification time when it was registered, which is how a later call
            notices the file changed without walking the tree
        source_size: Beside the mtime, because a restored file can carry an older stamp
        first_seen: When this content was registered — the date in the history
        last_seen: When a run last used it
        param_hash: Fingerprint of `strategy_config` — what the algo decides. The same value the
            ledger already carries, so a config and a run row can be compared directly
        scope_hash: Fingerprint of WHICH DATA runs — symbols, windows, tick caps, enabled flags.
            None for a profile, which has no scenario list to scope
        run_count: How many runs have named this id
    """
    config_id: str
    kind: RunConfigKind
    frozen_file: str
    source_name: str
    source_path: str
    source_mtime: float
    source_size: int
    first_seen: datetime
    last_seen: datetime
    param_hash: str = ''
    scope_hash: Optional[str] = None
    run_count: int = 0


@dataclass
class RunConfigHistory:
    """
    Every registered version of ONE source file, oldest first.

    Args:
        source_name: The file these versions belong to
        versions: Its entries, ordered by `first_seen`
    """
    source_name: str
    versions: List[RunConfigEntry] = field(default_factory=list)
