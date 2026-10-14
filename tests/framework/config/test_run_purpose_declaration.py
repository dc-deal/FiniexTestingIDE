"""
What a configuration declares about its runs (#576).

A scenario set and an AutoTrader profile say at their top level what the runs started from them
are FOR — `regular`, `fixture` or `certificate` — and why they exist (`description`). This suite
pins how both loaders read the two keys, that a configuration the user owns declares no purpose,
that neither key moves a profile's fingerprints, and the folder rule that keeps every configuration
a test can run a fixture.
"""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from python.configuration.app_config_manager import AppConfigManager
from python.configuration.autotrader.autotrader_config_loader import load_autotrader_config
from python.framework.autotrader.rendered_profile_builder import render_autotrader_profile
from python.framework.config_directory.config_directory_builder import config_kind_of
from python.framework.reporting.store.run_provenance_builder import _profile_fingerprint
from python.framework.types.config_directory_types import ConfigKind
from python.framework.types.run_purpose_types import RunPurpose
from python.scenario.scenario_config_loader import ScenarioConfigLoader

_ROOT = Path(__file__).resolve().parents[3]
_SCENARIO_SET = _ROOT / 'tests' / 'fixtures' / 'scenario_sets' / 'cascade' / 'no_overrides.json'
_PROFILE = _ROOT / 'configs' / 'autotrader_profiles' / 'mock' / 'mock_session_test.json'

# The folders the suites load their scenario sets and mock profiles from — every configuration
# in them is one a test may run, so every one of them declares itself a fixture.
_TEST_FOLDERS = [
    _ROOT / 'configs' / 'scenario_sets' / 'backtesting',
    _ROOT / 'configs' / 'autotrader_profiles' / 'mock',
    _ROOT / 'tests' / 'fixtures',
]
_CERTIFICATE_FOLDER = _ROOT / 'configs' / 'autotrader_profiles' / 'field_study'
_REGULAR_FOLDERS = [
    _ROOT / 'configs' / 'autotrader_profiles' / 'production',
    _ROOT / 'configs' / 'autotrader_profiles' / 'observation',
]


def _configurations_in(folder: Path) -> list:
    """
    Every scenario set and AutoTrader profile below a folder, by the directory's own markers.

    Args:
        folder: The folder to walk

    Returns:
        (path, parsed JSON) per configuration
    """
    found = []
    for path in sorted(folder.rglob('*.json')):
        if config_kind_of(path) is not None:
            found.append((path, json.loads(path.read_text(encoding='utf-8'))))
    return found


def _written(tmp_path: Path, source: Path, name: str, **changes) -> Path:
    """
    A copy of a configuration with its top-level keys changed — None removes a key.

    Args:
        tmp_path: Where the copy goes
        source: The configuration to copy
        name: The copy's file name
        changes: Top-level keys to set, or to remove with None

    Returns:
        The copy's path
    """
    data = json.loads(source.read_text(encoding='utf-8'))
    for key, value in changes.items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    path = tmp_path / name
    path.write_text(json.dumps(data, indent=2), encoding='utf-8')
    return path


class TestEveryTestConfigurationIsAFixture:
    """The folder rule, held in both directions — a test's run says what it is."""

    def test_the_test_folders_hold_configurations(self):
        assert all(_configurations_in(folder) for folder in _TEST_FOLDERS), \
            'a test folder holds no configuration — the walk itself is broken'

    def test_every_configuration_a_test_can_run_declares_itself_a_fixture(self):
        undeclared = [str(path.relative_to(_ROOT)) for folder in _TEST_FOLDERS
                      for path, data in _configurations_in(folder)
                      if data.get('run_purpose') != RunPurpose.FIXTURE.value]
        assert not undeclared, f'declare "run_purpose": "fixture" in: {undeclared}'

    def test_the_field_study_profiles_declare_a_certificate(self):
        profiles = _configurations_in(_CERTIFICATE_FOLDER)
        assert profiles
        assert all(data.get('run_purpose') == RunPurpose.CERTIFICATE.value
                   for _, data in profiles)

    def test_no_production_or_observation_profile_declares_another_purpose(self):
        """A bot's own sessions are regular — absent says so, and nothing else may be written."""
        declared = [str(path.relative_to(_ROOT)) for folder in _REGULAR_FOLDERS
                    for path, data in _configurations_in(folder)
                    if data.get('run_purpose', RunPurpose.REGULAR.value)
                    != RunPurpose.REGULAR.value]
        assert not declared


class TestTheScenarioSetLoaderReadsTheDeclaration:

    def test_the_declared_purpose_arrives_beside_a_description(self, tmp_path):
        path = _written(tmp_path, _SCENARIO_SET, 'declared_set.json',
                        run_purpose='certificate', description='Why this set **exists**.')
        assert ScenarioConfigLoader().load_config(str(path)).run_purpose is RunPurpose.CERTIFICATE

    def test_a_set_that_says_nothing_is_regular(self, tmp_path):
        path = _written(tmp_path, _SCENARIO_SET, 'silent_set.json',
                        run_purpose=None, description=None)
        assert ScenarioConfigLoader().load_config(str(path)).run_purpose is RunPurpose.REGULAR

    def test_an_unknown_purpose_is_refused_with_the_allowed_ones(self, tmp_path):
        path = _written(tmp_path, _SCENARIO_SET, 'misdeclared_set.json', run_purpose='fixtures')
        with pytest.raises(ValueError, match='regular, fixture, certificate'):
            ScenarioConfigLoader().load_config(str(path))

    def test_a_misspelt_top_level_key_is_refused_rather_than_read_as_absent(self, tmp_path):
        """Without the check `run_purpse` would read as absent — a fixture run as regular."""
        path = _written(tmp_path, _SCENARIO_SET, 'typo_set.json', run_purpose=None,
                        run_purpse='fixture')
        with pytest.raises(ValueError, match='run_purpse'):
            ScenarioConfigLoader().load_config(str(path))

    def test_a_description_that_is_not_text_is_refused(self, tmp_path):
        path = _written(tmp_path, _SCENARIO_SET, 'listed_set.json', description=['a', 'b'])
        with pytest.raises(ValueError, match='description must be text'):
            ScenarioConfigLoader().load_config(str(path))


class TestTheProfileLoaderReadsTheDeclaration:

    def test_the_declared_purpose_arrives_beside_a_description(self, tmp_path):
        path = _written(tmp_path, _PROFILE, 'declared_profile.json',
                        run_purpose='certificate', description='Why this profile exists.')
        assert load_autotrader_config(str(path)).run_purpose is RunPurpose.CERTIFICATE

    def test_a_description_that_is_not_text_is_refused(self, tmp_path):
        path = _written(tmp_path, _PROFILE, 'listed_profile.json', description={'a': 1})
        with pytest.raises(ValueError, match='description must be text'):
            load_autotrader_config(str(path))

    def test_a_profile_that_says_nothing_is_regular(self, tmp_path):
        path = _written(tmp_path, _PROFILE, 'silent_profile.json', run_purpose=None)
        assert load_autotrader_config(str(path)).run_purpose is RunPurpose.REGULAR

    def test_an_unknown_purpose_is_refused_with_the_allowed_ones(self, tmp_path):
        path = _written(tmp_path, _PROFILE, 'misdeclared_profile.json', run_purpose='test')
        with pytest.raises(ValueError, match='regular, fixture, certificate'):
            load_autotrader_config(str(path))

    def test_the_served_name_is_not_a_json_key(self, tmp_path):
        """The file says `description`; `config_description` is only the name it is served under."""
        path = _written(tmp_path, _PROFILE, 'field_named_profile.json',
                        config_description='not a key of a profile')
        with pytest.raises(ValueError, match='config_description'):
            load_autotrader_config(str(path))


@pytest.fixture
def user_algo_dir(tmp_path, monkeypatch) -> Path:
    """A user algo directory in the tmp tree, configured as the only one."""
    directory = tmp_path / 'user_algos'
    directory.mkdir()
    monkeypatch.setattr(AppConfigManager, 'get_user_algo_dirs', lambda self: [str(directory)])
    return directory


class TestAConfigurationTheUserOwnsDeclaresNoPurpose:
    """Its runs are always regular, so a declaration there is refused, whatever its value."""

    @pytest.mark.parametrize('purpose', ['regular', 'fixture', 'certificate'])
    def test_a_set_that_declares_one_is_refused(self, user_algo_dir, purpose):
        path = _written(user_algo_dir, _SCENARIO_SET, 'my_set.json',
                        scenario_set_name='my_set', run_purpose=purpose)
        with pytest.raises(ValueError, match='does not belong in a user algo directory'):
            ScenarioConfigLoader().load_config(str(path))

    def test_a_set_that_declares_none_is_regular(self, user_algo_dir):
        path = _written(user_algo_dir, _SCENARIO_SET, 'my_set.json',
                        scenario_set_name='my_set', run_purpose=None)
        assert ScenarioConfigLoader().load_config(str(path)).run_purpose is RunPurpose.REGULAR

    def test_a_profile_that_declares_one_is_refused(self, user_algo_dir):
        path = _written(user_algo_dir, _PROFILE, 'my_profile.json',
                        profile_name='my_profile', bot_id='mine01', run_purpose='fixture')
        with pytest.raises(ValueError, match='does not belong in a user algo directory'):
            load_autotrader_config(str(path))

    def test_a_profile_that_declares_none_is_regular(self, user_algo_dir):
        path = _written(user_algo_dir, _PROFILE, 'my_profile.json',
                        profile_name='my_profile', bot_id='mine01', run_purpose=None)
        assert load_autotrader_config(str(path)).run_purpose is RunPurpose.REGULAR


class TestTheDeclarationMovesNoFingerprint:
    """
    What a session is FOR and why its profile exists say nothing about how it trades. Neither may
    mark a deployment's session `operation_changed`, nor mint a new rendered version of the profile.
    """

    def test_the_operational_fingerprint_ignores_the_purpose(self):
        config = load_autotrader_config(str(_PROFILE))
        moved = replace(config, run_purpose=RunPurpose.CERTIFICATE)
        assert _profile_fingerprint(moved) == _profile_fingerprint(config)

    def test_the_rendered_profile_ignores_the_purpose(self):
        config = load_autotrader_config(str(_PROFILE))
        moved = replace(config, run_purpose=RunPurpose.CERTIFICATE)
        assert render_autotrader_profile(moved) == render_autotrader_profile(config)

    def test_a_reworded_description_moves_neither(self, tmp_path):
        first = load_autotrader_config(str(_written(tmp_path, _PROFILE, 'first_profile.json',
                                                    description='Why it exists.')))
        second = load_autotrader_config(str(_written(tmp_path, _PROFILE, 'second_profile.json',
                                                     description='Reworded entirely.')))
        assert _profile_fingerprint(first) == _profile_fingerprint(second)
        assert render_autotrader_profile(first) == render_autotrader_profile(second)
