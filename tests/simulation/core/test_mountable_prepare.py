"""
Mountable batch preparation tests (#417) — the validate → prepare_mount → execute seam.

Verifies the BatchOrchestrator split is behavior-preserving and the seam is reusable:
- run() == validate + prepare_mount() + execute()                 (split equivalence)
- one MountPackage reused across orchestrators → identical results (reuse + determinism, #368)
- DataIdentityKey ignores strategy_config, captures the data dimension (#418/#419 lookup key)
- the identity guard rejects a mount fed mismatched scenarios     (#419/#418 safety contract)
"""

import copy
from typing import List, Tuple

import pytest

from python.configuration.app_config_manager import AppConfigManager
from python.framework.batch.batch_orchestrator import BatchOrchestrator
from python.framework.exceptions.mount_errors import MountIdentityMismatchError
from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.types.mount_package_types import DataIdentityKey
from python.framework.types.scenario_types.scenario_set_types import ScenarioSet
from python.framework.validators.scenario_validator import ScenarioValidator
from python.scenario.scenario_config_loader import ScenarioConfigLoader

CORE_CONFIG = "backtesting/backtesting_validation_test.json"


def _build() -> Tuple[BatchOrchestrator, ScenarioSet]:
    """Build a fresh orchestrator + scenario set for the core config."""
    scenario_config = ScenarioConfigLoader().load_config(CORE_CONFIG)
    app_config = AppConfigManager()
    scenario_set = ScenarioSet(scenario_config, app_config)
    return BatchOrchestrator(scenario_set, app_config), scenario_set


def _result_signature(summary: BatchExecutionSummary) -> List[tuple]:
    """Deterministic per-scenario result signature: (name, success, trades, orders)."""
    signature = []
    for result in summary.process_result_list:
        tlr = result.tick_loop_results
        trades = len(tlr.trade_history) if tlr and tlr.trade_history is not None else -1
        orders = len(tlr.order_history) if tlr and tlr.order_history is not None else -1
        signature.append((result.scenario_name, bool(result.success), trades, orders))
    return signature


def test_split_equivals_run():
    """run() must produce the same results as validate + prepare_mount() + execute()."""
    orch_cold, _ = _build()
    cold = _result_signature(orch_cold.run())

    orch_split, scenario_set = _build()
    ScenarioValidator.validate_scenario_parameters(
        scenarios=scenario_set.get_valid_scenarios(), logger=scenario_set.logger)
    mount = orch_split.prepare_mount()
    split = _result_signature(orch_split.execute(mount, scenario_set.get_all_scenarios()))

    assert split == cold


def test_mount_reuse_is_deterministic():
    """execute() is pure: one MountPackage executed twice yields identical results (#368)."""
    orch, scenario_set = _build()
    ScenarioValidator.validate_scenario_parameters(
        scenarios=scenario_set.get_valid_scenarios(), logger=scenario_set.logger)
    mount = orch.prepare_mount()
    scenarios = scenario_set.get_all_scenarios()

    first = _result_signature(orch.execute(mount, scenarios))
    second = _result_signature(orch.execute(mount, scenarios))

    assert first == second


def test_data_identity_ignores_strategy_config():
    """The data identity must depend on the data dimension, never on strategy_config."""
    scenarios = ScenarioConfigLoader().load_config(CORE_CONFIG).scenarios
    scenario = scenarios[0]

    base = DataIdentityKey.from_scenario(scenario, bar_requirements=[])

    # Different strategy parameters → SAME data identity (mount reusable).
    other_params = copy.deepcopy(scenario)
    other_params.strategy_config = {
        **other_params.strategy_config,
        'decision_logic_config': {'changed_param': 12345},
    }
    assert DataIdentityKey.from_scenario(other_params, bar_requirements=[]) == base

    # Different data window → DIFFERENT data identity (mount NOT reusable).
    other_window = copy.deepcopy(scenario)
    other_window.max_ticks = (scenario.max_ticks or 1000) + 1
    assert DataIdentityKey.from_scenario(other_window, bar_requirements=[]) != base


def test_identity_guard_rejects_mismatched_scenarios():
    """execute() must reject scenarios whose data identity does not match the mount."""
    orch, scenario_set = _build()
    ScenarioValidator.validate_scenario_parameters(
        scenarios=scenario_set.get_valid_scenarios(), logger=scenario_set.logger)
    mount = orch.prepare_mount()

    mounted = next(
        scn for scn in scenario_set.get_all_scenarios()
        if scn.scenario_index in mount.scenario_packages
    )
    foreign = copy.deepcopy(mounted)
    foreign.symbol = 'XXXXXX'  # change the data dimension → identity no longer matches

    with pytest.raises(MountIdentityMismatchError):
        orch.execute(mount, [foreign])
