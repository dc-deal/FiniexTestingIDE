"""
Sweep-grid validator tests (#390) — STRUCTURAL fail-fast only.

Parameter existence / range is no longer checked here — it moved into the run's Phase 0
(see tests/framework/batch_validations/test_scenario_validator.py ::
TestValidateScenarioParameters). The grid validator only guards the spec shape.
"""

import pytest

from python.framework.logging.bootstrap_logger import get_global_logger
from python.framework.validators.sweep_grid_validator import validate_sweep_grid


@pytest.fixture
def log():
    return get_global_logger()


def test_valid_grid_passes(log):
    """Well-shaped decision + worker paths with non-empty value lists validate cleanly."""
    grid = {
        'decision_logic_config.min_confidence': [0.3, 0.5],
        'workers.bollinger_main.deviation': [2, 3],
    }
    validate_sweep_grid(grid, log)  # no raise


def test_unknown_param_passes_structural(log):
    """A non-existent param is NOT a structural error — it passes the grid validator and is
    rejected per-combination in the run's Phase 0 (failed run → error-flagged ledger row, #1)."""
    validate_sweep_grid({'decision_logic_config.does_not_exist': [1]}, log)  # no raise
    validate_sweep_grid({'workers.no_such_worker.deviation': [2]}, log)      # no raise


def test_out_of_range_value_passes_structural(log):
    """An out-of-range VALUE is NOT a structural error — handled per-combination at runtime."""
    validate_sweep_grid({'decision_logic_config.min_confidence': [0.3, 1.5]}, log)  # no raise


def test_bad_path_prefix_raises(log):
    """A grid path that is neither decision_logic_config nor workers is rejected."""
    with pytest.raises(ValueError):
        validate_sweep_grid({'execution_config.parallel_workers': [True]}, log)


def test_decision_path_too_short_raises(log):
    """A decision path must have at least two segments (the bare prefix is rejected)."""
    with pytest.raises(ValueError):
        validate_sweep_grid({'decision_logic_config': [1]}, log)


def test_worker_path_too_short_raises(log):
    """A worker path must have at least three segments."""
    with pytest.raises(ValueError):
        validate_sweep_grid({'workers.bollinger_main': [2]}, log)


def test_nested_paths_pass(log):
    """Nested decision + worker paths (sub-parameters) validate cleanly (#419)."""
    validate_sweep_grid({
        'decision_logic_config.risk.sl_pips': [100, 150],
        'workers.bollinger_main.periods.M30': [20, 50],
    }, log)  # no raise


def test_empty_value_list_raises(log):
    """A path mapping to an empty value list is rejected."""
    with pytest.raises(ValueError):
        validate_sweep_grid({'decision_logic_config.min_confidence': []}, log)
