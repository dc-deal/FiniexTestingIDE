"""Optimization config loader tests (#390) — spec parsing + structural guards."""

import json

import pytest
from pydantic import ValidationError

from python.configuration.optimization_config_loader import OptimizationConfigLoader

_GRID_SPEC = 'tests/fixtures/optimization/btcusd_mini_grid.json'


def test_load_spec_fields():
    """The fixture grid spec parses with its declared objective + grid."""
    spec = OptimizationConfigLoader().load_spec(_GRID_SPEC)
    assert spec.base_scenario_set.endswith('btcusd_mini_set.json')
    assert spec.objective == 'net_pnl'
    assert 'decision_logic_config.min_confidence' in spec.grid


def test_sweep_name_defaults_to_stem():
    """An unset sweep_name defaults to the spec file stem."""
    spec = OptimizationConfigLoader().load_spec(_GRID_SPEC)
    assert spec.sweep_name == 'btcusd_mini_grid'


def test_missing_spec_raises():
    """A non-existent spec path is a hard error."""
    with pytest.raises(FileNotFoundError):
        OptimizationConfigLoader().load_spec('does_not_exist_grid.json')


def test_unknown_key_rejected(tmp_path):
    """An unknown top-level key in the spec is rejected (extra='forbid')."""
    bad = tmp_path / 'bad_grid.json'
    bad.write_text(json.dumps({
        'base_scenario_set': 'x.json',
        'grid': {'decision_logic_config.sl_pips': [100]},
        'typo_field': 1,
    }))
    with pytest.raises(ValidationError):
        OptimizationConfigLoader().load_spec(str(bad))
