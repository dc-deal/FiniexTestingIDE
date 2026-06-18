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

from pathlib import Path

import pytest

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
