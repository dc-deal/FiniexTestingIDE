"""
The fixture production record (#576) — what each production of a catalog entry made.

A RECORD store of ONE append-only JSONL file at the root of the run tree, beside the run index:
`runs/fixture_productions.jsonl`. One file, because it is read whole and holds a handful of
lines per entry; at the run tree's root, because it describes runs in that tree and has to die
with it. Its location is derived from the run index's, never configured a second time — which
also puts it under a test session's isolated run tree without a fixture of its own.

The CURRENT fixture of an entry is derived from it: the newest production whose runs carried
every property. Nothing is written into a run, and a production that failed its check stays in
the record as what happened.
"""

from pathlib import Path
from typing import Dict, List, Optional, Set

from pydantic import ValidationError

from python.configuration.app_config_manager import AppConfigManager
from python.framework.types.fixture_catalog_types import FixtureProduction

PRODUCTION_RECORD_FILE = 'fixture_productions.jsonl'


class FixtureProductionStore:
    """The production record of the fixture catalog."""

    def __init__(self, path: Optional[Path] = None, declared_entries: Optional[Set[str]] = None):
        """
        Args:
            path: The record file; beside the configured run index when not given
            declared_entries: The entries the catalog still declares. A production of any other
                entry — renamed or removed since — is current no more, so its runs read as
                superseded and a prune may release them. None counts every entry as declared
        """
        self._path = Path(path) if path is not None else default_production_record_path()
        self._declared_entries = declared_entries

    def get_path(self) -> Path:
        """
        Where the record lives.

        Returns:
            The record file
        """
        return self._path

    def append(self, production: FixtureProduction) -> None:
        """
        Record one production, after everything else it did.

        Args:
            production: What the production made and whether it carried every property
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open('a', encoding='utf-8') as stream:
            stream.write(production.model_dump_json() + '\n')

    def read(self) -> List[FixtureProduction]:
        """
        Every recorded production, oldest first.

        A line that does not parse is left out — the tail of a write that was cut off, or a line
        this version cannot read — so the productions around it are still served as what happened.

        Returns:
            The productions
        """
        if not self._path.exists():
            return []
        productions = []
        for line in self._path.read_text(encoding='utf-8').splitlines():
            if not line.strip():
                continue
            try:
                productions.append(FixtureProduction.model_validate_json(line))
            except ValidationError:
                continue
        return productions

    def current(self) -> Dict[str, FixtureProduction]:
        """
        Each entry's CURRENT production — its newest verified one.

        Returns:
            Entry id → its current production; an entry with no verified production is absent
        """
        return _current_of(self.read(), self._declared_entries)

    def verified_run_ids(self) -> Set[str]:
        """
        Every run of every production that carried its properties — current or superseded. A
        consumer may still pin a superseded one until they have moved to the new ids, which only
        the operator knows; the pruner keeps them until it is told.

        Returns:
            Their run ids
        """
        return {run_id for production in self.read() if production.verified
                for run_id in production.run_ids}

    def current_run_ids(self) -> Set[str]:
        """
        Every run of each entry's CURRENT production — what a consumer pins now.

        Returns:
            Their run ids
        """
        return {run_id for production in self.current().values()
                for run_id in production.run_ids}

    def fixture_superseded_by_run(self) -> Dict[str, bool]:
        """
        For every run a production made: whether it is NOT its entry's current fixture.

        Derived from the record each time it is asked, never written into a run — an old run
        stays exactly what it was, and only reads as superseded.

        Returns:
            Run id → True for a run of an older production or of one that failed its check,
            False for a run of the entry's current production; a run no production made is absent
        """
        productions = self.read()
        current = _current_of(productions, self._declared_entries)
        superseded: Dict[str, bool] = {}
        for production in productions:
            pinned = current.get(production.entry_id)
            current_runs = set(pinned.run_ids) if pinned is not None else set()
            for run_id in production.run_ids:
                superseded[run_id] = run_id not in current_runs
        return superseded



def _current_of(productions: List[FixtureProduction],
                declared_entries: Optional[Set[str]]) -> Dict[str, FixtureProduction]:
    """
    Each declared entry's newest verified production, from the productions in record order.

    Args:
        productions: The record, oldest first
        declared_entries: The entries the catalog still declares; None counts every entry

    Returns:
        Entry id → its current production
    """
    current: Dict[str, FixtureProduction] = {}
    for production in productions:
        if production.verified and (declared_entries is None
                                     or production.entry_id in declared_entries):
            current[production.entry_id] = production
    return current

def default_production_record_path() -> Path:
    """
    The record's location: the root of the run tree, beside the run index.

    Returns:
        The record file
    """
    run_index = AppConfigManager().get_file_logging_config_object().run_index
    return Path(run_index).parent / PRODUCTION_RECORD_FILE
