"""
How Each Store Writes — Durably or Not, Declared Once (#632)

A crash of the process never leaves half a file; a crash of the machine can leave an EMPTY one, when
the filesystem commits a rename before the bytes it renames. A record or a carry-over cannot be
rebuilt, so its writes are forced to the disk; a derived store is rebuilt from its source and its
writes are not. That answer belongs to the store's kind, declared once beside it, and every write
names its store rather than deciding for itself — so the answers here, and the guard that no file is
renamed into place by hand outside the declared exceptions.
"""

import ast
from pathlib import Path

import pytest

from python.framework.store.store_registrations import build_registrations
from python.framework.types.store_types import (
    SPECIAL_STORE_DURABILITY,
    STORE_KINDS,
    StoreId,
    StoreKind,
)

_PYTHON_ROOT = Path(__file__).resolve().parents[3] / 'python'

# Where a file is still renamed into place outside the shared atomic writer: each module with the
# number of renames it makes, and its reason. A new hand-written rename — in any module, an exempt one
# included — fails, and so does one that went away until its entry is lowered or removed: the list
# may only shrink.
_RENAMES_OUTSIDE_THE_WRITER = {
    # The shared atomic writer itself
    'framework/utils/atomic_file_utils.py': 1,
    # The index's temporary name is shared on purpose: it is how a writer without the lock is caught
    'framework/store/abstract_store_index.py': 1,
    # The identity is minted first-writer-wins by a link, which a replace would undo
    'configuration/host_identity_manager.py': 1,
    # Not a store: a stamp file the operator addresses for a watcher outside the app
    'framework/autotrader/operator_alerts/liveness_pinger.py': 1,
    # A fixture's folder is swapped whole, not a file
    'framework/fixture_catalog/golden_samples/golden_sample_io.py': 1,
    # A download kept under its partial name, so the next import resumes it
    'data_management/importers/import_coordinator.py': 1,
    # A derived bar file under the fixed partial name the next render's cleanup removes
    'data_management/importers/bar_importer.py': 1,
    # An unreadable broker cache moved aside, not written
    'configuration/autotrader/kraken_config_fetcher.py': 1,
    # A legacy index file moved aside (#583 owns these managers)
    'data_management/index/tick_index_manager.py': 1,
    'data_management/index/bars_index_manager.py': 1,
    # The re-import tool: a member moved or streamed under its partial name into the inbox, and its
    # own manifest beside the conveyor (#120 owns its lifetime)
    'experiments/raw_archive_reimport/raw_archive_reimport.py': 3,
}

# The tools under python/experiments/ that run again, and are scanned like production code
_TOOLS_THAT_RUN_AGAIN = ('experiments/venue_probes/', 'experiments/raw_archive_reimport/')


def _renames_in(source: str) -> int:
    """
    Count the calls that rename a file: `os.replace` / `os.rename`, and a path's own `.replace(x)`
    / `.rename(x)` — one positional argument and no keywords, which a string's replace never has.

    Args:
        source: A module's source

    Returns:
        How many such calls it makes
    """
    count = 0
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in ('replace', 'rename'):
            continue
        receiver = node.func.value
        if isinstance(receiver, ast.Name) and receiver.id == 'os':
            count += 1
        elif (len(node.args) == 1 and not node.keywords
              and not (isinstance(receiver, ast.Name) and receiver.id == 'dataclasses')):
            count += 1
    return count


class TestEveryStoreAnswers:

    def test_every_store_has_its_kind_stated_once(self):
        assert set(STORE_KINDS) == set(StoreId)
        for store_id, descriptor in build_registrations().items():
            assert descriptor.kind is STORE_KINDS[store_id]

    @pytest.mark.parametrize('kind,durable', [
        (StoreKind.RECORD, True), (StoreKind.CARRY_OVER, True), (StoreKind.ARCHIVE, True),
        (StoreKind.DERIVED, False), (StoreKind.SPECIAL, None)])
    def test_the_kind_decides(self, kind, durable):
        assert kind.writes_durably() is durable

    def test_every_special_store_answers_for_itself_and_no_other_does(self):
        special = {store for store, kind in STORE_KINDS.items() if kind is StoreKind.SPECIAL}
        assert set(SPECIAL_STORE_DURABILITY) == special
        for store in StoreId:
            assert isinstance(store.writes_durably(), bool)

    def test_an_index_that_holds_what_nothing_else_does_writes_durably(self):
        """A rebuild cannot restore what was only ever written into such an index."""
        for descriptor in build_registrations().values():
            index = descriptor.build_index()
            if index is None:
                continue
            assert type(index).WRITES_DURABLY is bool(descriptor.rebuild_loses), descriptor.store_id


class TestNoFileIsRenamedIntoPlaceByHand:

    def test_every_rename_is_the_shared_writers_or_declared(self):
        found = {}
        for path in sorted(_PYTHON_ROOT.rglob('*.py')):
            relative = path.relative_to(_PYTHON_ROOT).as_posix()
            if relative.startswith('experiments/') and not relative.startswith(_TOOLS_THAT_RUN_AGAIN):
                continue
            count = _renames_in(path.read_text(encoding='utf-8'))
            if count:
                found[relative] = count
        assert found == _RENAMES_OUTSIDE_THE_WRITER, (
            'write through write_file_atomic, naming the store — or declare the rename with its reason')
