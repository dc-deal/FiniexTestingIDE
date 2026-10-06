"""
An AutoTrader session records what it RAN with, not only what it was given (#547).

The profile file is the input to a cascade: the `app_config.autotrader` defaults lie under it, and
every parameter it leaves unset gets its schema default only when the factory builds the
component. The rendered document is the result, frozen before the header names it. These tests
hold it to three things: it carries every layer, its strategy is exactly what the factories
build, and the operational hash the ledger records can be recomputed from it.
"""

import json
from pathlib import Path

import pytest

from python.configuration.autotrader.autotrader_config_loader import load_autotrader_config
from python.configuration.market_config_manager import MarketConfigManager
from python.framework.autotrader.rendered_profile_builder import (
    assert_rendered_parameters_match,
    render_autotrader_profile,
)
from python.framework.decision_logic.core.simple_consensus import SimpleConsensus
from python.framework.exceptions.rendered_config_errors import RenderedConfigMismatchError
from python.framework.factory.decision_logic_factory import DecisionLogicFactory
from python.framework.factory.worker_factory import WorkerFactory
from python.framework.logging.bootstrap_logger import get_global_logger
from python.framework.reporting.store.run_provenance_builder import (
    _NON_OPERATIONAL_FIELDS,
    _profile_fingerprint,
)
from python.framework.utils.config_fingerprint_utils import generate_config_fingerprint, to_plain

_PROFILE = 'configs/autotrader_profiles/mock/minimal_warmup_test.json'
_MOCK_PROFILES = sorted(Path('configs/autotrader_profiles/mock').glob('*.json'))


def _built_components(config):
    """
    Build the decision logic and the workers the way the session does, without a pipeline.

    Args:
        config: The loaded profile

    Returns:
        (decision logic, workers by instance name)
    """
    logger = get_global_logger()
    strategy = config.strategy_config
    workers = WorkerFactory(logger).create_workers_from_config(strategy_config=strategy)
    logic = DecisionLogicFactory(logger).create_logic(
        logic_type=strategy['decision_logic_type'], logger=logger,
        logic_config=strategy.get('decision_logic_config', {}))
    return logic, workers


class TestTheDocumentCarriesEveryLayer:

    def test_a_default_no_file_sets_is_in_the_document(self):
        config = load_autotrader_config(_PROFILE)
        raw = json.loads(Path(_PROFILE).read_text())['strategy_config']['decision_logic_config']
        schema = SimpleConsensus.get_parameter_schema()
        unset = sorted(set(schema) - set(raw))
        assert unset, 'the fixture profile has to leave at least one parameter to its default'

        rendered = render_autotrader_profile(config)['strategy_config']['decision_logic_config']
        for name in unset:
            assert rendered[name] == to_plain(schema[name].default)

    def test_the_app_config_layer_is_merged_into_the_blocks(self):
        # The fixture sets ONE execution key and no order guard at all — so the rest of
        # `execution` and the whole `order_guard` block can only have come from app_config.
        config = load_autotrader_config(_PROFILE)
        raw = json.loads(Path(_PROFILE).read_text())
        assert set(raw['execution']) == {'bar_max_history'} and 'order_guard' not in raw

        blocks = render_autotrader_profile(config)['blocks']
        assert blocks['execution']['bar_max_history'] == raw['execution']['bar_max_history']
        assert set(blocks['execution']) > set(raw['execution'])
        assert blocks['order_guard'] == to_plain(config.order_guard)
        assert 'config_path' not in blocks and 'strategy_config' not in blocks

    def test_every_type_key_is_the_one_the_factory_injects(self):
        config = load_autotrader_config(_PROFILE)
        strategy = render_autotrader_profile(config)['strategy_config']

        assert (strategy['decision_logic_config']['decision_logic_type']
                == config.strategy_config['decision_logic_type'])
        for name, worker_type in config.strategy_config['worker_instances'].items():
            assert strategy['workers'][name]['worker_type'] == worker_type

    def test_the_broker_entry_is_recorded_with_its_resolved_values(self):
        config = load_autotrader_config(_PROFILE)
        entry = render_autotrader_profile(config)['market_entry']

        assert entry == to_plain(MarketConfigManager().get_broker_entry(config.broker_type))
        assert {'dry_run', 'config_mode'} <= set(entry)

    def test_the_operational_hash_can_be_recomputed_from_the_document(self):
        # One projection for both: the ledger's profile_hash and the rendered blocks go through
        # the same `to_plain`, so the hash a run recorded is checkable against what it froze.
        config = load_autotrader_config(_PROFILE)
        blocks = render_autotrader_profile(config)['blocks']
        operational = {k: v for k, v in blocks.items() if k not in _NON_OPERATIONAL_FIELDS}

        assert generate_config_fingerprint(operational) == _profile_fingerprint(config)

    def test_the_same_profile_renders_the_same_document(self):
        config = load_autotrader_config(_PROFILE)
        first = render_autotrader_profile(config)
        second = render_autotrader_profile(config)

        assert generate_config_fingerprint(first) == generate_config_fingerprint(second)
        json.dumps(first)


class TestTheRecordIsWhatWasBuilt:

    @pytest.mark.parametrize('profile', _MOCK_PROFILES, ids=lambda p: p.stem)
    def test_the_rendered_strategy_matches_the_constructed_components(self, profile):
        # Every shipped mock profile, so every CORE logic and worker they name is covered: the
        # renderer asks the factories' `resolve_parameters`, construction runs `create_*`, and
        # both go through one helper. A drift between the two is what this catches.
        config = load_autotrader_config(profile)
        logic, workers = _built_components(config)

        assert_rendered_parameters_match(render_autotrader_profile(config), logic, workers)

    def test_a_parameter_that_moved_after_rendering_refuses_the_session(self):
        config = load_autotrader_config(_PROFILE)
        rendered = render_autotrader_profile(config)
        logic, workers = _built_components(config)
        rendered['strategy_config']['decision_logic_config']['min_confidence'] = 0.99

        with pytest.raises(RenderedConfigMismatchError, match="'min_confidence'"):
            assert_rendered_parameters_match(rendered, logic, workers)

    def test_a_worker_the_record_does_not_name_refuses_the_session(self):
        config = load_autotrader_config(_PROFILE)
        rendered = render_autotrader_profile(config)
        logic, workers = _built_components(config)
        rendered['strategy_config']['workers'].pop(next(iter(workers)))

        with pytest.raises(RenderedConfigMismatchError, match='worker set'):
            assert_rendered_parameters_match(rendered, logic, workers)
