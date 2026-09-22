"""
FiniexTestingIDE - Run Config Store (#538)

Every configuration that can start a run, with an identity, a home and a history.

Before this store a scenario set and an AutoTrader profile were files at a path, and a path is
not an identity. Three things followed from that one gap. Nothing could say two runs used the
SAME configuration — measured on this tree: 53 per-run config snapshots holding 23 distinct
contents, one of them eight times. A config edited yesterday left no trace that it changed. And
the backtest half of a parity measurement could not name its own strategy identity at all, where
the live half has carried `param_hash` and `profile_hash` since #497.

**The store owns its own bytes.** Registering a config FREEZES it here under its content id
rather than pointing at where it was found. That is not tidiness: a source may live in
`user_algos/`, which is a separate repository this project never writes into, and an index whose
entries live outside its own root could not die with its store (§44).

**Resolution by name is the second job and the one that is measured.** `_resolve_path` used to
locate a config with a recursive glob, twice per set, over a tree of 107 directories — 11.6 s of
a 19.5 s scenario listing, against 0.063 s spent actually reading the JSON. Here it is an index
lookup plus one `stat` on the known path: 111 ms for all 67 configs, where a single glob costs
613 ms.
"""

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from python.framework.store.run_config_index import FROZEN_SUBDIR, RunConfigIndex
from python.framework.types.run_config_types import (
    RunConfigEntry,
    RunConfigHistory,
    RunConfigKind,
)
from python.framework.utils.config_fingerprint_utils import generate_config_fingerprint

# The keys that decide WHICH DATA a scenario runs, as opposed to what the algo decides with it.
# Kept here rather than inferred, because a reader has to know the difference to read a history
# correctly and inference would guess (§49).
_SCOPE_KEYS = ('symbol', 'start_date', 'end_date', 'max_ticks', 'enabled', 'data_mode')


class RunConfigStore:
    """
    The registered run configurations, keyed by the content they hold.

    Args:
        root: The `run_configs` directory
    """

    def __init__(self, root: Path):
        self._root = Path(root)
        self._index = RunConfigIndex(self._root)

    def get_index(self) -> RunConfigIndex:
        """
        The store's index.

        Returns:
            The index object, for callers that read rather than register
        """
        return self._index

    def register(self, source: Path, kind: RunConfigKind) -> RunConfigEntry:
        """
        Record what a file currently holds, freezing its content if that content is new.

        Idempotent by construction: the id IS the content, so registering an unchanged file twice
        writes no second copy and only moves `last_seen`. A changed file mints a new id and lands
        beside its predecessor, which is what turns the index into a history.

        Args:
            source: The config file as it was found
            kind: Which pipeline it starts

        Returns:
            The entry for the content this file now holds
        """
        raw = json.loads(source.read_text(encoding='utf-8'))
        config_id = generate_config_fingerprint(raw)
        stat = source.stat()
        now = datetime.now(timezone.utc)

        frozen = self._frozen_path(config_id, kind)
        if not frozen.exists():
            self._freeze(raw, frozen)

        known = self._index.by_id(config_id)
        entry = RunConfigEntry(
            config_id=config_id,
            kind=kind,
            frozen_file=frozen.name,
            source_name=source.name,
            source_path=str(source),
            source_mtime=stat.st_mtime,
            source_size=stat.st_size,
            # A content id that is already known keeps the date it was FIRST seen — that date is
            # the one the history reads, and re-registering an unchanged file must not move it.
            first_seen=known.first_seen if known else now,
            last_seen=now,
            param_hash=_param_hash(raw),
            scope_hash=_scope_hash(raw) if kind is RunConfigKind.SCENARIO_SET else None,
            run_count=known.run_count if known else 0,
        )
        self._index.upsert(entry)
        return entry

    def sync(self, sources: List[Path], kind: RunConfigKind) -> int:
        """
        Bring the store up to date with an authoritative enumeration, cheaply.

        Called with the list SOMEONE ELSE already resolved — the scenario-set finder knows the
        precedence between `user_configs`, `user_algos` and `configs`, and it pays one recursive
        walk to establish it. Re-deriving that here would be the same walk a second time (§19),
        so the enumeration is passed in and this only records what changed.

        The steady state costs one `stat` per file and NO write: a file whose size and
        modification time match its indexed row is not re-read, not re-hashed and not re-written.
        Measured: 111 ms of stats for 67 configs, against 613 ms for a single recursive glob.

        Args:
            sources: The files, as the caller resolved them
            kind: Which pipeline they start

        Returns:
            How many were registered — zero when nothing changed
        """
        frame = self._index.read()
        known = {}
        if not frame.empty:
            for row in frame.itertuples():
                if row.source_name:
                    known[row.source_name] = (float(row.source_mtime), int(row.source_size))

        changed = 0
        for source in sources:
            try:
                stat = source.stat()
            except OSError:
                continue
            if known.get(source.name) == (stat.st_mtime, stat.st_size):
                continue
            try:
                self.register(source, kind)
                changed += 1
            except (OSError, ValueError):
                # A config this store cannot parse is not this store's problem to report: the
                # loader refuses it with a message naming the file, and swallowing it here keeps
                # one broken config from making every other one unfindable.
                continue
        return changed

    def resolve(self, source_name: str) -> Optional[Path]:
        """
        Where a config file lives, without walking the tree.

        Answers from the index and then CONFIRMS with one `stat`, because an index entry is a
        record of where a file was, not a promise that it still is. A moved or deleted file
        returns None so the caller can fall back to searching — the store accelerates the common
        case and never becomes the only way to find anything.

        Args:
            source_name: The file name a caller asks for

        Returns:
            The path, or None when the store cannot vouch for one
        """
        entry = self._index.current_of(source_name)
        if entry is None or not entry.source_path:
            return None
        path = Path(entry.source_path)
        return path if path.exists() else None

    def history(self, source_name: str) -> RunConfigHistory:
        """
        Every version one source file has had, oldest first.

        Args:
            source_name: The file name

        Returns:
            Its history; empty when the name was never registered
        """
        return RunConfigHistory(
            source_name=source_name, versions=self._index.versions_of(source_name))

    def frozen_path_of(self, config_id: str) -> Optional[Path]:
        """
        The store's own copy of one registered configuration.

        Args:
            config_id: The content id

        Returns:
            The path to the frozen bytes, or None when the id is unknown
        """
        entry = self._index.by_id(config_id)
        if entry is None:
            return None
        return self._frozen_path(config_id, entry.kind)

    def note_run(self, config_id: str) -> None:
        """
        Record that a run used this configuration.

        Args:
            config_id: The content id the run named
        """
        entry = self._index.by_id(config_id)
        if entry is None:
            return
        entry.run_count += 1
        entry.last_seen = datetime.now(timezone.utc)
        self._index.upsert(entry)

    def _frozen_path(self, config_id: str, kind: RunConfigKind) -> Path:
        """
        Where one content id's copy belongs.

        Args:
            config_id: The content id
            kind: Which pipeline it starts

        Returns:
            The path, whether or not it exists yet
        """
        return self._root / FROZEN_SUBDIR[kind] / f'{config_id}.json'

    @staticmethod
    def _freeze(raw: Dict[str, Any], target: Path) -> None:
        """
        Write the store's own copy, atomically.

        The NORMALISED form is written — sorted keys, one indent — not the source bytes, because
        that is what the id was computed over. A frozen copy whose bytes did not hash back to its
        own file name would be a record that cannot check itself.

        Args:
            raw: The parsed configuration
            target: Where the copy belongs
        """
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix('.tmp')
        tmp.write_text(json.dumps(raw, sort_keys=True, indent=1), encoding='utf-8')
        os.replace(tmp, target)


def _param_hash(raw: Dict[str, Any]) -> str:
    """
    Fingerprint what the algo DECIDES — the same value the ledger carries.

    A scenario set's strategy config may be declared globally, per scenario, or both; the ledger
    already folds that the same way (one hash when every scenario agrees, a hash over the list
    when they do not), and this mirrors it so a config row and a run row are directly comparable.

    Args:
        raw: The parsed configuration

    Returns:
        SHA256 hex digest
    """
    scenarios = raw.get('scenarios')
    if not isinstance(scenarios, list):
        return generate_config_fingerprint(raw.get('strategy_config') or {})

    base = raw.get('global', {}).get('strategy_config') or raw.get('strategy_config') or {}
    per = [generate_config_fingerprint(s.get('strategy_config') or base)
           for s in scenarios if isinstance(s, dict)]
    if not per:
        return generate_config_fingerprint(base)
    return per[0] if len(set(per)) == 1 else generate_config_fingerprint({'per_scenario': per})


def _scope_hash(raw: Dict[str, Any]) -> str:
    """
    Fingerprint WHICH DATA runs — symbols, windows, tick caps, enabled flags.

    The reason this exists beside `param_hash`: a config can change in four ways and they mean
    different things. Adding a scenario changes the scope and not the decisions; raising a
    worker's period changes the decisions and not the scope; renaming a scenario changes NEITHER
    and only the content id moves. Without this second hash the last two cases are
    indistinguishable, and "different file, same meaning" is exactly what a reader of a history
    needs to be told.

    Args:
        raw: The parsed configuration

    Returns:
        SHA256 hex digest over the scoping keys of every scenario, in order
    """
    scenarios = raw.get('scenarios') or []
    scoped: List[Dict[str, Any]] = [
        {k: s.get(k) for k in _SCOPE_KEYS if k in s}
        for s in scenarios if isinstance(s, dict)
    ]
    return generate_config_fingerprint({'scenarios': scoped})


def prune_store(root: Path) -> int:
    """
    Remove the store entirely — it is rebuildable from the sources by re-registering.

    Exists for the tests and for an operator who wants a clean slate; the store is a RECORD, so
    nothing calls this on its own initiative.

    Args:
        root: The store root

    Returns:
        1 when something was removed, 0 when there was nothing there
    """
    if not Path(root).exists():
        return 0
    shutil.rmtree(root)
    return 1
