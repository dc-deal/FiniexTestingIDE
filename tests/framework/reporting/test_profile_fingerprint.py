"""
Two fingerprints over one live profile, and why one wide hash would be worse than either (#497).

`param_hash` covers `strategy_config`. It is the value #512 compares a backtest against, so
widening it to the whole profile would put a run beyond comparison the moment a stop level
moved — a change that alters nothing the strategy decides.

`profile_hash` covers the rest: the safety thresholds, the order guard, the execution and
tick-source settings, the capital declaration. Those change what a session DOES without
changing what it decides, and the question a reader actually asks of a month-long deployment —
"why did the breaker not fire on day 19" — is a question about exactly them.

So the pair answers two questions, and the property pinned here is that each answers ONLY its
own. A value answering both would answer neither.
"""

import json
from pathlib import Path

import pytest

from python.configuration.autotrader.autotrader_config_loader import load_autotrader_config
from python.framework.reporting.store.run_provenance_builder import _plain, _profile_fingerprint

PROFILE_ROOT = Path(__file__).resolve().parents[3] / 'configs' / 'autotrader_profiles'
BASE_PROFILE = PROFILE_ROOT / 'backtesting' / 'minimal_warmup_test.json'


@pytest.fixture
def make_config(tmp_path):
    """
    Factory: load a real profile with one section replaced.

    A real loaded config rather than a stand-in, because the fingerprint walks the config
    object's own fields — the blocks come in both shapes §6 allows, and a hand-built
    namespace would exercise neither.
    """
    counter = {'n': 0}

    def _make(**sections):
        raw = json.loads(BASE_PROFILE.read_text())
        for name, value in sections.items():
            if isinstance(value, dict) and isinstance(raw.get(name), dict):
                raw[name] = {**raw[name], **value}
            else:
                raw[name] = value
        counter['n'] += 1
        path = tmp_path / f'profile_{counter["n"]}.json'
        path.write_text(json.dumps(raw))
        return load_autotrader_config(str(path))
    return _make


class TestItIsStable:
    """A fingerprint that moves on its own cannot attribute anything."""

    def test_the_same_profile_at_two_paths_gives_the_same_hash(self, make_config):
        """
        The factory writes each profile to its own file, so this is two identical profiles
        at two paths. They must agree: `config_path` is excluded on purpose, or a moved or
        copied file would read as an operational change in the middle of a deployment.
        """
        assert _profile_fingerprint(make_config()) == _profile_fingerprint(make_config())

    def test_it_is_a_hex_digest(self, make_config):
        digest = _profile_fingerprint(make_config())
        assert len(digest) == 64
        assert all(c in '0123456789abcdef' for c in digest)


class TestWhatItCovers:
    """The operational half — what a session DOES."""

    def test_a_changed_safety_threshold_moves_it(self, make_config):
        before = _profile_fingerprint(make_config())
        after = _profile_fingerprint(make_config(safety={'max_drawdown_pct': 7.5}))
        assert before != after

    def test_a_changed_execution_setting_moves_it(self, make_config):
        before = _profile_fingerprint(make_config())
        after = _profile_fingerprint(make_config(execution={'bar_max_history': 60}))
        assert before != after

    def test_the_deployment_declaration_is_part_of_it(self, make_config):
        """
        Whether these sessions are meant to be one history is an operational fact about
        the profile, and a deployment that changed its own definition mid-flight should be
        visible on the row that changed it.
        """
        before = _profile_fingerprint(make_config())
        after = _profile_fingerprint(make_config(deployment={'continuous': True}))
        assert before != after


class TestWhatItDeliberatelyIgnores:
    """The strategy half, and the run's own identity."""

    def test_a_changed_strategy_parameter_does_not_move_it(self, make_config):
        """
        `param_hash` already carries that, and counting it twice would make the pair
        useless: every strategy change would look like an operational one as well, so a
        reader could no longer tell which of the two happened.
        """
        before = _profile_fingerprint(make_config())
        after = _profile_fingerprint(make_config(strategy_config={
            **json.loads(BASE_PROFILE.read_text())['strategy_config'],
            'decision_logic_config': {'rsi_oversold': 25, 'rsi_overbought': 75,
                                      'bollinger_lower_threshold': 0.3,
                                      'bollinger_upper_threshold': 0.7,
                                      'min_confidence': 0.5, 'lot_size': 0.001,
                                      'min_entry_capital': 1000}}))
        assert before == after

    def test_the_profile_name_is_not_operational(self, make_config):
        before = _profile_fingerprint(make_config())
        after = _profile_fingerprint(make_config(name='renamed_but_identical'))
        assert before == after


class TestTheProjection:
    """`_plain` has to survive both config shapes §6 allows, plus what sits inside them."""

    def test_a_pydantic_block_is_projected_by_value(self, make_config):
        projected = _plain(make_config().execution)
        assert isinstance(projected, dict)
        assert 'bar_max_history' in projected

    def test_a_settings_dataclass_is_projected_field_by_field(self, make_config):
        projected = _plain(make_config().safety)
        assert isinstance(projected, dict)
        assert 'max_drawdown_pct' in projected

    def test_scalars_and_containers_pass_through(self):
        assert _plain('x') == 'x'
        assert _plain(3) == 3
        assert _plain(None) is None
        assert _plain([1, 'a']) == [1, 'a']

    def test_a_dict_is_ordered_so_the_digest_is_not_insertion_dependent(self):
        """
        Two configs that differ only in the order their keys were written must fingerprint
        alike — otherwise a reformatted JSON file would read as a changed profile.
        """
        assert list(_plain({'b': 1, 'a': 2})) == ['a', 'b']

    def test_the_whole_result_is_json_serialisable(self, make_config):
        """
        The fallback to `repr` is what keeps the function total; this is the check that it
        stays a fallback. A value whose repr carries an address would make two identical
        runs fingerprint differently — invisible until a deployment blamed a change that
        never happened.
        """
        config = make_config()
        for field_name in ('execution', 'safety', 'order_guard', 'capital', 'tick_source'):
            json.dumps(_plain(getattr(config, field_name)))
