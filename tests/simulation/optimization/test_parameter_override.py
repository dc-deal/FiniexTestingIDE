"""Parameter override tests (#390) — dotted-path set + in-memory combo isolation."""

from python.framework.optimization.parameter_override import apply_overrides, set_by_path
from python.scenario.scenario_config_loader import ScenarioConfigLoader

_BASE_SET = 'tests/fixtures/optimization/btcusd_mini_set.json'


def test_set_by_path_existing_nested():
    """A dotted path overwrites an existing nested value."""
    d = {'decision_logic_config': {'sl_pips': 20}}
    set_by_path(d, 'decision_logic_config.sl_pips', 100)
    assert d['decision_logic_config']['sl_pips'] == 100


def test_set_by_path_creates_intermediate():
    """Missing intermediate dicts are created."""
    d = {}
    set_by_path(d, 'workers.bollinger_main.deviation', 2.5)
    assert d == {'workers': {'bollinger_main': {'deviation': 2.5}}}


def test_apply_overrides_writes_into_each_scenario():
    """The combination's values land in every scenario's strategy_config."""
    base = ScenarioConfigLoader().load_config(_BASE_SET)
    cfg = apply_overrides(base, {'decision_logic_config.min_confidence': 0.45}, '__sweep_c000')
    for scenario in cfg.scenarios:
        assert scenario.strategy_config['decision_logic_config']['min_confidence'] == 0.45


def test_apply_overrides_does_not_mutate_base():
    """The base config is untouched (deep-copy isolation across combinations)."""
    base = ScenarioConfigLoader().load_config(_BASE_SET)
    original = base.scenarios[0].strategy_config['decision_logic_config']['min_confidence']
    apply_overrides(base, {'decision_logic_config.min_confidence': 0.99}, '__sweep_c000')
    assert base.scenarios[0].strategy_config['decision_logic_config']['min_confidence'] == original


def test_apply_overrides_tags_scenario_set_name():
    """The label is appended to scenario_set_name → unique run dir per combination."""
    base = ScenarioConfigLoader().load_config(_BASE_SET)
    cfg = apply_overrides(base, {}, '__sweep_20260101_c003')
    assert cfg.scenario_set_name == f"{base.scenario_set_name}__sweep_20260101_c003"
