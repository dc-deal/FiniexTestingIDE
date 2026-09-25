"""
Mountable batch preparation tests (#417) — the validate → prepare_mount → execute seam.

Verifies the BatchOrchestrator split is behavior-preserving and the seam is reusable:
- run() == validate + prepare_mount() + execute()                 (split equivalence)
- one MountPackage reused across orchestrators → identical results (reuse + determinism, #368)
- DataIdentityKey ignores strategy_config, captures the data dimension (#418/#419 lookup key)
- the identity guard rejects a mount fed mismatched scenarios     (#419/#418 safety contract)
- a scenario the data quality phase rejects AFTER its package was built is reported, not a crash
"""

import copy
from pathlib import Path
from typing import List, Tuple

import pytest

from python.configuration.app_config_manager import AppConfigManager
from python.framework.batch.batch_orchestrator import BatchOrchestrator
from python.framework.exceptions.mount_errors import MountIdentityMismatchError
from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.types.mount_package_types import DataIdentityKey
from python.framework.types.api.report_types import RunReporting
from python.scenario.scenario_set import ScenarioSet
from python.framework.validators.scenario_validator import ScenarioValidator
from python.scenario.scenario_config_loader import ScenarioConfigLoader

CORE_CONFIG = 'backtesting/backtesting_validation_test.json'

# One window at the head of the ETHUSD archive (short M30 warmup) beside an ordinary one (§34).
WARMUP_REJECTED_CONFIG = str(
    Path(__file__).resolve().parents[2] / 'fixtures' / 'scenario_sets' / 'mountable_prepare'
    / 'warmup_rejected_after_mount.json')


def _build(config: str = CORE_CONFIG) -> Tuple[BatchOrchestrator, ScenarioSet]:
    """Build a fresh orchestrator + scenario set for a config (the core config by default)."""
    scenario_config = ScenarioConfigLoader().load_config(config)
    app_config = AppConfigManager()
    scenario_set = ScenarioSet(scenario_config, app_config, reporting=RunReporting.NONE)
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


def test_a_scenario_rejected_after_its_package_was_built_is_reported_not_a_crash():
    """
    The data quality phase runs AFTER the packages are built, so a scenario it rejects keeps its
    package but never receives a data identity. The guard used to skip only scenarios WITHOUT a
    package, read the missing identity as a mismatch and aborted the whole batch with
    MountIdentityMismatchError — a data problem crashing the run, which §33 forbids.
    """
    orch, scenario_set = _build(WARMUP_REJECTED_CONFIG)

    summary = orch.run()

    rejected, ordinary = scenario_set.get_all_scenarios()
    reasons = ' '.join(error for result in rejected.validation_result for error in result.errors)
    assert 'insufficient for indicator stabilization' in reasons, \
        'the fixture must be rejected by the data quality phase, after its package was built'
    assert ordinary.is_valid()
    results = {result.scenario_name: result for result in summary.process_result_list}
    assert results['ordinary_window'].success
