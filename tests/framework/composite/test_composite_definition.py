"""
The composite definition: which configuration values define a composite, and the floor no file can
loosen (#615).
"""

import json
from pathlib import Path

import pytest

import python.configuration.composite_definition_manager as definition_module
from python.configuration.composite_definition_manager import CompositeDefinitionManager
from python.framework.constants.composite_constants import FLOOR_DOCUMENT_PATHS
from python.framework.types.composite_types import CompositeDocument
from python.framework.types.config_types.composite_definition_config_types import (
    CompositeDefinitionConfig,
    CompositeTier,
)

_SHIPPED = 'configs/composite_definition.json'


@pytest.fixture(autouse=True)
def _fresh_definition():
    """The definition is cached per process; every test reads its own files."""
    CompositeDefinitionManager.reload()
    yield
    CompositeDefinitionManager.reload()


def _write(path: Path, content: dict) -> str:
    path.write_text(json.dumps(content), encoding='utf-8')
    return str(path)


class TestTheShippedDefinition:

    def test_it_loads_with_rules_for_both_pipelines(self):
        manager = CompositeDefinitionManager(config_path=_SHIPPED)
        assert manager.get_rules(CompositeDocument.SCENARIO)
        assert manager.get_rules(CompositeDocument.SESSION)

    def test_a_stress_block_is_excluding_in_both(self):
        manager = CompositeDefinitionManager(config_path=_SHIPPED)
        assert manager.get_rules(CompositeDocument.SCENARIO)['stress_test_config'] is CompositeTier.EXCLUDING
        assert (manager.get_rules(CompositeDocument.SESSION)['scenario_settings.stress_test_config']
                is CompositeTier.EXCLUDING)

    def test_the_section_comments_are_no_rules(self):
        raw = json.loads(Path(_SHIPPED).read_text())
        assert '_comment' in raw['scenario']
        manager = CompositeDefinitionManager(config_path=_SHIPPED)
        assert '_comment' not in manager.get_rules(CompositeDocument.SCENARIO)


class TestTheFloorStaysInCode:

    @pytest.mark.parametrize('path', FLOOR_DOCUMENT_PATHS)
    def test_a_rule_for_a_floor_path_is_refused(self, path):
        with pytest.raises(ValueError, match='belongs to the floor'):
            CompositeDefinitionConfig(scenario={path: 'neutral'})

    def test_the_floor_tier_cannot_be_declared(self):
        with pytest.raises(ValueError, match='floor is fixed in code'):
            CompositeDefinitionConfig(session={'safety': 'floor'})

    @pytest.mark.parametrize('path', ['', '.safety', 'safety.', 'safety..max'])
    def test_a_rule_that_is_no_path_is_refused(self, path):
        with pytest.raises(ValueError, match='is not a path'):
            CompositeDefinitionConfig(session={path: 'neutral'})

    def test_an_unknown_section_is_refused(self):
        with pytest.raises(ValueError):
            CompositeDefinitionConfig(scenarios={'symbol': 'neutral'})

    def test_an_unknown_tier_is_refused(self):
        with pytest.raises(ValueError):
            CompositeDefinitionConfig(scenario={'symbol': 'sometimes'})


class TestTheOverrideAndTheFingerprint:

    def test_the_workspace_override_changes_one_rule_and_keeps_the_rest(self, tmp_path, monkeypatch):
        monkeypatch.setattr(definition_module, 'is_config_isolation_active', lambda: False)
        user = _write(tmp_path / 'composite_definition.json', {'session': {'capital': 'defining'}})
        manager = CompositeDefinitionManager(config_path=_SHIPPED, user_config_path=user)
        rules = manager.get_rules(CompositeDocument.SESSION)
        assert rules['capital'] is CompositeTier.DEFINING
        assert rules['safety'] is CompositeTier.DEFINING

    def test_an_override_that_loosens_the_floor_is_refused(self, tmp_path, monkeypatch):
        monkeypatch.setattr(definition_module, 'is_config_isolation_active', lambda: False)
        user = _write(tmp_path / 'composite_definition.json',
                      {'scenario': {'strategy_config.decision_logic_type': 'neutral'}})
        with pytest.raises(ValueError, match='belongs to the floor'):
            CompositeDefinitionManager(config_path=_SHIPPED, user_config_path=user).get_definition()

    def test_each_pipeline_has_its_own_fingerprint(self, tmp_path):
        shipped = json.loads(Path(_SHIPPED).read_text())
        changed = json.loads(Path(_SHIPPED).read_text())
        changed['session']['capital'] = 'defining'
        before = CompositeDefinitionManager(config_path=_write(tmp_path / 'a.json', shipped))
        scenario_before = before.get_fingerprint(CompositeDocument.SCENARIO)
        session_before = before.get_fingerprint(CompositeDocument.SESSION)
        CompositeDefinitionManager.reload()
        after = CompositeDefinitionManager(config_path=_write(tmp_path / 'b.json', changed))
        # A change to the session half leaves every backtest key as it was.
        assert after.get_fingerprint(CompositeDocument.SCENARIO) == scenario_before
        assert after.get_fingerprint(CompositeDocument.SESSION) != session_before
