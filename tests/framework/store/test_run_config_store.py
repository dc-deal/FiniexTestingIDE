"""
Run configs as a store (#538).

A configuration that starts a run used to be a file at a path, and a path is not an identity:
nothing could say two runs used the same configuration, an edited file left no trace, and the
backtest half of a parity measurement could not name its own strategy identity at all.

Two properties carry the whole design and this suite is organised around them.

**The identity is the CONTENT.** Registering the same bytes twice is one entry; changing them
mints a second beside the first, and that accumulation IS the history. Normalised rather than
raw, so a reformatted file stays the same configuration.

**Three hashes, because a change means three things.** The content id says the bytes differ,
`param_hash` what the algo DECIDES, `scope_hash` WHICH DATA runs. Only the three together can
say that a renamed scenario changed nothing a run would do — which is the question a reader of a
history actually has.
"""

import json
import time
from pathlib import Path

import pytest

from python.framework.store.run_config_store import RunConfigStore
from python.framework.types.run_config_types import RunConfigKind

_BASE = {
    'scenario_set_name': 'demo',
    'global': {'strategy_config': {'workers': {'rsi': {'period': 14}}}},
    'scenarios': [
        {'name': 'a', 'symbol': 'BTCUSD', 'start_date': '2026-01-01', 'enabled': True},
    ],
}


def _write(path: Path, payload: dict, indent: int = 2) -> Path:
    """
    Write a config and make sure its mtime moves.

    Args:
        path: Where to write
        payload: The configuration
        indent: JSON indentation, so a pure FORMATTING change can be expressed

    Returns:
        The path written
    """
    path.write_text(json.dumps(payload, indent=indent), encoding='utf-8')
    time.sleep(0.01)
    return path


@pytest.fixture
def store(tmp_path) -> RunConfigStore:
    """A store in a throwaway root."""
    return RunConfigStore(tmp_path / 'run_configs')


@pytest.fixture
def source(tmp_path) -> Path:
    """A scenario set file that tests rewrite."""
    sources = tmp_path / 'sources'
    sources.mkdir(parents=True, exist_ok=True)
    return _write(sources / 'my_set.json', _BASE)


class TestTheIdentityIsTheContent:

    def test_the_same_bytes_register_once(self, store, source):
        first = store.register(source, RunConfigKind.SCENARIO_SET)
        second = store.register(source, RunConfigKind.SCENARIO_SET)

        assert first.config_id == second.config_id
        assert len(store.get_index().entries()) == 1
        assert len(store.get_index().frozen_files()) == 1

    def test_reformatting_alone_is_the_same_configuration(self, store, source):
        """
        The id is computed over the NORMALISED content, so indentation is not identity. A raw-byte
        id would fill the history with versions that mean nothing.
        """
        first = store.register(source, RunConfigKind.SCENARIO_SET)
        _write(source, _BASE, indent=8)
        second = store.register(source, RunConfigKind.SCENARIO_SET)

        assert first.config_id == second.config_id

    def test_a_changed_value_mints_a_second_version(self, store, source):
        store.register(source, RunConfigKind.SCENARIO_SET)
        changed = json.loads(json.dumps(_BASE))
        changed['global']['strategy_config']['workers']['rsi']['period'] = 21
        _write(source, changed)
        store.register(source, RunConfigKind.SCENARIO_SET)

        assert len(store.history('my_set.json').versions) == 2

    def test_the_first_seen_date_of_a_known_version_does_not_move(self, store, source):
        """It is the date the HISTORY reads; re-registering an unchanged file must not touch it."""
        first = store.register(source, RunConfigKind.SCENARIO_SET)
        again = store.register(source, RunConfigKind.SCENARIO_SET)

        assert again.first_seen == first.first_seen
        assert again.last_seen >= first.last_seen

    def test_the_frozen_copy_hashes_back_to_its_own_file_name(self, store, source):
        """A record that cannot check itself is not a record."""
        entry = store.register(source, RunConfigKind.SCENARIO_SET)
        frozen = store.frozen_path_of(entry.config_id)

        from python.framework.utils.config_fingerprint_utils import generate_config_fingerprint
        assert generate_config_fingerprint(json.loads(frozen.read_text())) == entry.config_id
        assert frozen.stem == entry.config_id


class TestTheThreeHashesSeparateFourKindsOfChange:
    """
    The distinction the operator asked for, and the reason one hash is not enough: a rename and
    a comment change the bytes and nothing else, while an added scenario and a changed parameter
    each move exactly one of the other two.
    """

    @staticmethod
    def _register(store, source, mutate) -> tuple:
        payload = json.loads(json.dumps(_BASE))
        mutate(payload)
        _write(source, payload)
        e = store.register(source, RunConfigKind.SCENARIO_SET)
        return e.config_id, e.param_hash, e.scope_hash

    def test_a_rename_moves_the_content_id_and_nothing_else(self, store, source):
        base = self._register(store, source, lambda p: None)
        renamed = self._register(store, source,
                                 lambda p: p['scenarios'][0].__setitem__('name', 'b'))

        assert renamed[0] != base[0], 'the bytes did not change'
        assert renamed[1] == base[1] and renamed[2] == base[2], (
            'a rename changed what the run would do')

    def test_a_comment_moves_the_content_id_and_nothing_else(self, store, source):
        base = self._register(store, source, lambda p: None)
        noted = self._register(store, source, lambda p: p.__setitem__('_comment', 'why'))

        assert noted[0] != base[0]
        assert noted[1] == base[1] and noted[2] == base[2]

    def test_an_added_scenario_moves_the_scope_and_not_the_decisions(self, store, source):
        base = self._register(store, source, lambda p: None)
        wider = self._register(store, source, lambda p: p['scenarios'].append(
            {'name': 'b', 'symbol': 'ETHUSD', 'start_date': '2026-02-01', 'enabled': True}))

        assert wider[2] != base[2], 'the scope did not move'
        assert wider[1] == base[1], 'adding a scenario changed the decisions'

    def test_a_parameter_moves_the_decisions_and_not_the_scope(self, store, source):
        base = self._register(store, source, lambda p: None)
        tuned = self._register(
            store, source,
            lambda p: p['global']['strategy_config']['workers']['rsi'].__setitem__('period', 21))

        assert tuned[1] != base[1], 'the decisions did not move'
        assert tuned[2] == base[2], 'a parameter changed the scope'

    def test_a_profile_has_no_scope_hash(self, store, tmp_path):
        """It holds one symbol and no scenario list — a scope hash over nothing would be a lie."""
        profile = _write(tmp_path / 'p.json', {'name': 'bot', 'symbol': 'BTCUSD'})
        entry = store.register(profile, RunConfigKind.AUTOTRADER_PROFILE)

        assert entry.scope_hash is None


class TestResolutionIsALookupAndNeverTheOnlyWay:
    """
    The measured half. Resolution used to be a recursive glob — 11.6 s of a 19.5 s listing — and
    is now an index read plus one stat. What must NOT happen is that the store becomes load-bearing:
    an entry that points at a file which moved has to yield, not mislead.
    """

    def test_a_registered_name_resolves_without_a_walk(self, store, source):
        store.register(source, RunConfigKind.SCENARIO_SET)

        assert store.resolve('my_set.json') == source

    def test_a_moved_file_resolves_to_nothing_rather_than_to_a_stale_path(self, store, source):
        store.register(source, RunConfigKind.SCENARIO_SET)
        source.unlink()

        assert store.resolve('my_set.json') is None

    def test_an_unknown_name_resolves_to_nothing(self, store):
        assert store.resolve('never_seen.json') is None

    def test_sync_registers_only_what_changed(self, store, source):
        assert store.sync([source], RunConfigKind.SCENARIO_SET) == 1
        assert store.sync([source], RunConfigKind.SCENARIO_SET) == 0, (
            'an unchanged file was re-registered')

        changed = json.loads(json.dumps(_BASE))
        changed['scenarios'][0]['symbol'] = 'ETHUSD'
        _write(source, changed)
        assert store.sync([source], RunConfigKind.SCENARIO_SET) == 1

    def test_sync_survives_a_config_it_cannot_parse(self, store, source, tmp_path):
        """One broken file must not make every other one unfindable."""
        broken = tmp_path / 'broken.json'
        broken.write_text('{ not json', encoding='utf-8')

        store.sync([broken, source], RunConfigKind.SCENARIO_SET)

        assert store.resolve('my_set.json') == source


class TestTheIndexDescribesItsStore:

    def test_a_fresh_store_is_not_stale(self, store, source):
        store.register(source, RunConfigKind.SCENARIO_SET)

        assert store.get_index().staleness_reason() is None

    def test_a_missing_frozen_copy_is_reported(self, store, source):
        """The index would otherwise describe bytes nobody can read back."""
        entry = store.register(source, RunConfigKind.SCENARIO_SET)
        store.frozen_path_of(entry.config_id).unlink()

        assert 'no frozen copy' in (store.get_index().staleness_reason() or '')

    def test_a_logic_version_bump_invalidates_the_index(self, store, source, monkeypatch):
        store.register(source, RunConfigKind.SCENARIO_SET)
        index = store.get_index()
        assert index.is_current()

        monkeypatch.setattr(type(index), 'LOGIC_VERSION', index.LOGIC_VERSION + 1)
        assert not index.is_current()

    def test_a_rebuild_finds_every_frozen_copy(self, store, source):
        """
        The repair path, and it is LOSSY by nature: the frozen bytes carry the identity, while
        `first_seen` and `source_path` were observations made at registration and exist nowhere
        else. The rebuild says so by leaving them empty rather than inventing them.
        """
        store.register(source, RunConfigKind.SCENARIO_SET)
        changed = json.loads(json.dumps(_BASE))
        changed['scenarios'][0]['symbol'] = 'ETHUSD'
        _write(source, changed)
        store.register(source, RunConfigKind.SCENARIO_SET)

        assert store.get_index().rebuild() == 2
        rebuilt = store.get_index().entries()
        assert len(rebuilt) == 2
        assert all(e.source_name == '' for e in rebuilt)


class TestARunIsCountedAgainstItsConfiguration:

    def test_note_run_increments_and_registration_preserves_the_count(self, store, source):
        entry = store.register(source, RunConfigKind.SCENARIO_SET)
        store.note_run(entry.config_id)
        store.note_run(entry.config_id)
        store.register(source, RunConfigKind.SCENARIO_SET)

        assert store.get_index().by_id(entry.config_id).run_count == 2

    def test_an_unknown_id_is_ignored_rather_than_raising(self, store):
        store.note_run('not_a_registered_id')
