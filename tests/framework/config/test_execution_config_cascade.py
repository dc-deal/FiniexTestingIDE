"""
FiniexTestingIDE - Execution Config Cascade Tests
Black-box coverage for the 3-level scenario_set cascade:
    app_config (Level 1) → scenario_set global (Level 2) → scenarios[i] (Level 3)

Tests drive ScenarioConfigLoader.load_config() against JSON fixtures in
tests/fixtures/scenario_sets/cascade/ and assert the merged execution_config
on the resulting SingleScenario. This exercises deep_merge, check_unknown_keys,
and validate_merged_config end-to-end via the loader's public surface.

Scope: execution_config lane only. The other cascade lanes
(trade_simulator_config, order_guard, stress_test_config, strategy_config)
follow the same pattern and deserve equivalent coverage in a follow-up.
"""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from python.framework.logging.global_logger import GlobalLogger
from python.framework.trading_env.simulation.order_latency_simulator import OrderLatencySimulator
from python.framework.types.config_types.backtesting_config_types import TradeSimulatorDefaults
from python.scenario.scenario_config_loader import ScenarioConfigLoader

_FIXTURE_DIR = Path(__file__).resolve().parents[2] / 'fixtures' / 'scenario_sets' / 'cascade'


def _load(filename: str):
    """Load a cascade fixture and return the first (and only) scenario."""
    loader = ScenarioConfigLoader()
    result = loader.load_config(str(_FIXTURE_DIR / filename))
    assert len(result.scenarios) == 1, 'cascade fixtures carry exactly one scenario'
    return result.scenarios[0]


class TestExecutionConfigCascade:
    """3-level cascade: app_config → scenario_set global → scenarios[i]."""

    def test_app_defaults_apply_when_no_overrides(self):
        """Neither global nor scenario overrides — Level 1 (app defaults) must be visible."""
        scenario = _load('no_overrides.json')
        exec_cfg = scenario.execution_config
        # App defaults from configs/app_config.json::default_scenario_execution_config
        assert exec_cfg['parallel_workers'] is False
        assert exec_cfg['worker_parallel_threshold_ms'] == 1.0
        assert exec_cfg['adaptive_parallelization'] is True
        assert exec_cfg['strict_parameter_validation'] is True
        assert exec_cfg['tick_processing_budget_ms'] == 0.0
        # Sub-group (#137): assert only that the performance_tracking switches CASCADE
        # (are present) — not their value. They are operator-mutable switches (flipped in
        # app_config to debug), so pinning the live value here would break the suite
        # whenever the operator toggles one. Their value-cascade is asserted against a
        # controlled fixture in test_sub_group_per_key_merge (app_config out of play).
        assert 'tick_loop_profiling' in exec_cfg['performance_tracking']
        assert 'worker_decision_tracking' in exec_cfg['performance_tracking']

    def test_global_overrides_app_defaults(self):
        """Global sets parallel_workers and tick_processing_budget_ms — Level 2 wins over Level 1."""
        scenario = _load('global_overrides_app.json')
        exec_cfg = scenario.execution_config
        # Global override
        assert exec_cfg['parallel_workers'] is True
        assert exec_cfg['tick_processing_budget_ms'] == 0.5
        # Untouched keys still from app defaults
        assert exec_cfg['adaptive_parallelization'] is True
        assert exec_cfg['strict_parameter_validation'] is True

    def test_scenario_overrides_global_and_app(self):
        """Scenario sets parallel_workers=False — Level 3 wins over Level 2 wins over Level 1."""
        scenario = _load('scenario_overrides_global.json')
        exec_cfg = scenario.execution_config
        # Scenario override
        assert exec_cfg['parallel_workers'] is False
        # Inherited from global (Level 2)
        assert exec_cfg['tick_processing_budget_ms'] == 0.5
        # Inherited from app defaults (Level 1)
        assert exec_cfg['adaptive_parallelization'] is True

    def test_sub_group_per_key_merge(self):
        """#137 — performance_tracking is a nested sub-group; scenario overrides one key
        while inheriting the other from global. Verifies deep_merge recurses into sub-groups."""
        scenario = _load('sub_group_per_key_merge.json')
        perf = scenario.execution_config['performance_tracking']
        # Scenario override
        assert perf['worker_decision_tracking'] is True
        # Inherited from global (NOT silently dropped by the merge)
        assert perf['tick_loop_profiling'] is True

    def test_unknown_key_hard_fails_with_provenance(self):
        """Typo 'parallel_workerz' must raise ValueError with location info before merge happens."""
        with pytest.raises(ValueError) as exc_info:
            _load('unknown_key_typo.json')
        msg = str(exc_info.value)
        assert 'global.execution_config' in msg
        assert 'parallel_workerz' in msg

    def test_a_misspelt_global_section_hard_fails_rather_than_read_as_absent(self, tmp_path):
        """Each section's keys are checked against its schema, but the SECTION names under
        `global` were not: `execution_confg` loaded, and its whole block was silently not
        applied. Every scenario then ran on the app defaults instead."""
        data = json.loads((_FIXTURE_DIR / 'global_overrides_app.json').read_text(encoding='utf-8'))
        data['global']['execution_confg'] = data['global'].pop('execution_config')
        path = tmp_path / 'misspelt_section.json'
        path.write_text(json.dumps(data, indent=2), encoding='utf-8')

        with pytest.raises(ValueError) as exc_info:
            ScenarioConfigLoader().load_config(str(path))
        msg = str(exc_info.value)
        assert 'Unknown keys in global:' in msg
        assert 'execution_confg' in msg


class TestAnOrderHasLatency:
    """
    An order's inbound latency is at least 1 ms, and its greatest no less than its least (#460):
    live, every order travels to the venue, and an order sent with none would fill at the price
    its decision saw. A scenario set asking for less refuses to load at whichever level of the
    cascade it asks, before any scenario runs — and a simulator built directly refuses it too.
    """

    def _set_with(self, tmp_path, global_block=None, scenario_block=None) -> Path:
        """
        The cascade fixture with latency settings written into it.

        Args:
            tmp_path: Where the set is written
            global_block: Settings for the global trade simulator block, or None
            scenario_block: Settings for the scenario's trade simulator block, or None

        Returns:
            The written set's path
        """
        data = json.loads((_FIXTURE_DIR / 'global_overrides_app.json').read_text(encoding='utf-8'))
        if global_block is not None:
            data['global']['trade_simulator_config'].update(global_block)
        if scenario_block is not None:
            data['scenarios'][0].setdefault('trade_simulator_config', {}).update(scenario_block)
        path = tmp_path / 'latency_set.json'
        path.write_text(json.dumps(data, indent=2), encoding='utf-8')
        return path

    def test_a_set_asking_for_no_latency_refuses_to_load(self, tmp_path):
        path = self._set_with(tmp_path, global_block={
            'inbound_latency_min_ms': 0, 'inbound_latency_max_ms': 0})

        with pytest.raises(ValueError) as exc_info:
            ScenarioConfigLoader().load_config(str(path))

        message = str(exc_info.value)
        assert 'trade_simulator_config[' in message
        assert 'inbound_latency_min_ms is 0' in message and 'at least 1 ms' in message

    def test_so_does_a_scenario_asking_for_it_alone(self, tmp_path):
        path = self._set_with(tmp_path, scenario_block={'inbound_latency_min_ms': 0})

        with pytest.raises(ValueError, match='inbound_latency_min_ms is 0'):
            ScenarioConfigLoader().load_config(str(path))

    def test_a_greatest_latency_below_the_least_refuses_to_load(self, tmp_path):
        path = self._set_with(tmp_path, global_block={
            'inbound_latency_min_ms': 80, 'inbound_latency_max_ms': 20})

        with pytest.raises(ValueError, match='inbound_latency_max_ms is 20, below'):
            ScenarioConfigLoader().load_config(str(path))

    def test_one_millisecond_loads(self, tmp_path):
        path = self._set_with(tmp_path, global_block={
            'inbound_latency_min_ms': 1, 'inbound_latency_max_ms': 1})

        scenario = ScenarioConfigLoader().load_config(str(path)).scenarios[0]

        assert scenario.trade_simulator_config['inbound_latency_min_ms'] == 1

    def test_the_app_default_is_held_to_it(self):
        with pytest.raises(ValidationError, match='inbound_latency_min_ms is -5'):
            TradeSimulatorDefaults(inbound_latency_min_ms=-5, inbound_latency_max_ms=10)

    @pytest.mark.parametrize('least, greatest', [(0, 0), (-5, 10), (80, 20)])
    def test_a_simulator_built_directly_refuses_it_too(self, least, greatest):
        with pytest.raises(ValueError, match='inbound_latency_m'):
            OrderLatencySimulator(
                seeds={'inbound_latency_seed': 42}, logger=GlobalLogger(name='LatencyFloorTest'),
                inbound_latency_min_ms=least, inbound_latency_max_ms=greatest)
