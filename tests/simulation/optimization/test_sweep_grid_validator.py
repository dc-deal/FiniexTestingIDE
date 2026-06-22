"""Sweep-grid validator tests (#390) — fail-fast against the component parameter schemas."""

import pytest

from python.framework.logging.bootstrap_logger import get_global_logger
from python.framework.validators.sweep_grid_validator import validate_sweep_grid
from python.scenario.scenario_config_loader import ScenarioConfigLoader

_BASE_SET = 'tests/fixtures/optimization/btcusd_mini_set.json'


@pytest.fixture
def strategy_config():
    """The base set's strategy_config (CORE/aggressive_trend + rsi + bollinger)."""
    return ScenarioConfigLoader().load_config(_BASE_SET).scenarios[0].strategy_config


@pytest.fixture
def log():
    return get_global_logger()


def test_valid_grid_passes(strategy_config, log):
    """In-range decision + worker params validate cleanly."""
    grid = {
        'decision_logic_config.min_confidence': [0.3, 0.5],
        'workers.bollinger_main.deviation': [2, 3],
    }
    validate_sweep_grid(grid, strategy_config, log)  # no raise


def test_out_of_range_value_passes_structural(strategy_config, log):
    """An out-of-range VALUE is NOT a structural error — it passes the grid validator and is
    handled per-combination at runtime (failed run → error-flagged ledger row, #1)."""
    validate_sweep_grid(
        {'decision_logic_config.min_confidence': [0.3, 1.5]}, strategy_config, log)  # no raise


def test_unknown_decision_param_raises(strategy_config, log):
    """A param not in the decision schema is rejected."""
    with pytest.raises(ValueError):
        validate_sweep_grid(
            {'decision_logic_config.does_not_exist': [1]}, strategy_config, log)


def test_unknown_worker_instance_raises(strategy_config, log):
    """A worker instance name not in worker_instances is rejected."""
    with pytest.raises(ValueError):
        validate_sweep_grid(
            {'workers.no_such_worker.deviation': [2]}, strategy_config, log)


def test_bad_path_prefix_raises(strategy_config, log):
    """A grid path that is neither decision_logic_config nor workers is rejected."""
    with pytest.raises(ValueError):
        validate_sweep_grid({'execution_config.parallel_workers': [True]}, strategy_config, log)


def test_empty_value_list_raises(strategy_config, log):
    """A path mapping to an empty value list is rejected."""
    with pytest.raises(ValueError):
        validate_sweep_grid({'decision_logic_config.min_confidence': []}, strategy_config, log)
