"""
Parameter override (#390) — inject a combination's grid values into a base config.

In-memory only: deep-copy the loaded base scenario set and write each combination value
into every scenario's `strategy_config` at its dotted path. No temp files; the base
config is never mutated. The scenario set name is tagged per combination so each combo
gets a unique run directory (and ledger fragment).
"""

import copy
from typing import Any, Dict

from python.framework.types.scenario_types.scenario_set_types import LoadedScenarioConfig


def set_by_path(target: Dict[str, Any], dotted_path: str, value: Any) -> None:
    """
    Set a value at a dotted path inside a nested dict (intermediate dicts created as needed).

    Args:
        target: The dict to mutate
        dotted_path: Path like 'decision_logic_config.sl_band_mult' or 'workers.tunnel.deviation'
        value: The value to set
    """
    keys = dotted_path.split('.')
    node = target
    for key in keys[:-1]:
        if not isinstance(node.get(key), dict):
            node[key] = {}
        node = node[key]
    node[keys[-1]] = value


def apply_overrides(
    base: LoadedScenarioConfig,
    combo: Dict[str, Any],
    label: str,
) -> LoadedScenarioConfig:
    """
    Deep-copy the base config and apply one combination's overrides into every scenario.

    Args:
        base: The loaded base scenario set (never mutated)
        combo: One grid combination (dotted path → value)
        label: Suffix appended to scenario_set_name → unique run dir per combination

    Returns:
        A new LoadedScenarioConfig with the combination's parameters applied
    """
    cfg = copy.deepcopy(base)
    cfg.scenario_set_name = f"{base.scenario_set_name}{label}"
    for scenario in cfg.scenarios:
        for dotted_path, value in combo.items():
            set_by_path(scenario.strategy_config, dotted_path, value)
    return cfg
