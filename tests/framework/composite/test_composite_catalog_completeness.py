"""
Every value a rendered document holds has a composite tier, and every rule of the definition
reaches a value (#615).

A value no rule reaches would count as defining by default — safe, because it can only split a
composite, but a decision nobody made. A rule that reaches nothing is a rule for a value that no
longer exists. Both directions are held over the documents the shipped configurations actually
render: every scenario of every scenario set the loader accepts, and every AutoTrader profile.
"""

from pathlib import Path
from typing import Dict, List, Set

import pytest

from python.configuration.app_config_manager import AppConfigManager
from python.configuration.autotrader.autotrader_config_loader import load_autotrader_config
from python.configuration.composite_definition_manager import CompositeDefinitionManager
from python.configuration.market_config_manager import MarketConfigManager
from python.framework.autotrader.run_record.rendered_profile_builder import render_autotrader_profile
from python.framework.config_directory.config_directory_builder import config_kind_of
from python.framework.constants.composite_constants import FLOOR_DOCUMENT_PATHS
from python.framework.types.composite_types import CompositeDocument
from python.framework.types.config_directory_types import ConfigKind
from python.framework.utils.composite_key_builder import document_leaves
from python.scenario.rendered_scenario_set_builder import render_scenario
from python.scenario.scenario_config_loader import ScenarioConfigLoader

_ROOT = Path(__file__).resolve().parents[3]
_SET_FOLDERS = [_ROOT / 'configs', _ROOT / 'tests' / 'fixtures']
_PROFILE_FOLDER = _ROOT / 'configs' / 'autotrader_profiles'


def _scenario_paths() -> Set[str]:
    """
    Every leaf path of every scenario the shipped scenario sets render.

    Returns:
        The paths
    """
    execution_defaults = AppConfigManager().get_scenario_execution_defaults()
    market_config = MarketConfigManager()
    paths: Set[str] = set()
    for folder in _SET_FOLDERS:
        for path in sorted(folder.rglob('*.json')):
            if config_kind_of(path) is not ConfigKind.SCENARIO_SET:
                continue
            try:
                loaded = ScenarioConfigLoader().load_config(str(path))
            except Exception:
                continue
            for scenario in loaded.scenarios:
                try:
                    document = render_scenario(scenario, execution_defaults, market_config)
                except ValueError:
                    continue
                paths |= set(document_leaves(document, CompositeDocument.SCENARIO))
    return paths


def _session_paths() -> Set[str]:
    """
    Every leaf path of every shipped AutoTrader profile, rendered.

    Returns:
        The paths
    """
    paths: Set[str] = set()
    for path in sorted(_PROFILE_FOLDER.rglob('*.json')):
        try:
            document = render_autotrader_profile(load_autotrader_config(str(path)))
        except Exception:
            continue
        paths |= set(document_leaves(document, CompositeDocument.SESSION))
    return paths


@pytest.fixture(scope='module')
def documents() -> Dict[CompositeDocument, Set[str]]:
    return {CompositeDocument.SCENARIO: _scenario_paths(),
            CompositeDocument.SESSION: _session_paths()}


@pytest.fixture(scope='module')
def definition() -> CompositeDefinitionManager:
    CompositeDefinitionManager.reload()
    return CompositeDefinitionManager(config_path=str(_ROOT / 'configs' / 'composite_definition.json'))


def _reaches(rule: str, path: str) -> bool:
    return path == rule or path.startswith(rule + '.')


@pytest.mark.parametrize('document_kind', list(CompositeDocument))
class TestBothDirections:

    def test_documents_were_found(self, documents, document_kind):
        assert documents[document_kind]

    def test_every_value_has_a_tier(self, documents, definition, document_kind):
        rules = definition.get_rules(document_kind)
        unreached: List[str] = sorted(
            path for path in documents[document_kind]
            if path not in FLOOR_DOCUMENT_PATHS and not any(_reaches(rule, path) for rule in rules))
        assert not unreached, f'values no rule of the {document_kind.value} definition reaches: {unreached}'

    def test_every_rule_reaches_a_value(self, documents, definition, document_kind):
        rules = definition.get_rules(document_kind)
        dead = sorted(rule for rule in rules
                      if not any(_reaches(rule, path) for path in documents[document_kind]))
        assert not dead, f'rules of the {document_kind.value} definition that reach no value: {dead}'

    def test_the_floor_is_in_every_kind_of_document(self, documents, document_kind):
        for floor_path in FLOOR_DOCUMENT_PATHS:
            assert floor_path in documents[document_kind]
