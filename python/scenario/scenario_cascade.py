"""
FiniexTestingIDE - Scenario Configuration Cascade System
Deep merging utility for multi-level config inheritance
"""

import copy
from typing import Dict, Any


class ScenarioCascade:
    """
    Handles deep merging of configuration dictionaries across multiple levels.

    Supports 3-level cascade for execution_config:
        app_config → global → scenario

    Supports 2-level cascade for strategy_config, trade_simulator_config
    and stress_test_config:
        global → scenario

    Key principle: Scenario values ALWAYS override global, global ALWAYS overrides app defaults.
    Merging is DEEP - nested dicts are merged recursively, not replaced.
    """

    @staticmethod
    def merge_execution_config(
        app_defaults: Dict[str, Any],
        global_config: Dict[str, Any],
        scenario_config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        3-level cascade merge for execution_config.

        Args:
            app_defaults: Defaults from app_config.json::scenario_execution_defaults
            global_config: Global execution_config from scenario_set
            scenario_config: Scenario-specific execution_config

        Returns:
            Merged execution_config with scenario taking precedence
        """
        # Start with app defaults
        result = copy.deepcopy(app_defaults)

        # Merge global config (overrides app defaults)
        if global_config:
            result = ScenarioCascade._deep_merge(result, global_config)

        # Merge scenario config (overrides everything)
        if scenario_config:
            result = ScenarioCascade._deep_merge(result, scenario_config)

        return result

    @staticmethod
    def merge_strategy_config(
        global_config: Dict[str, Any],
        scenario_config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        2-level cascade merge for strategy_config.

        Args:
            global_config: Global strategy_config from scenario_set
            scenario_config: Scenario-specific strategy_config

        Returns:
            Merged strategy_config with scenario taking precedence
        """
        result = copy.deepcopy(global_config)

        if scenario_config:
            result = ScenarioCascade._deep_merge(result, scenario_config)

        return result

    @staticmethod
    def merge_trade_simulator_config(
        global_config: Dict[str, Any],
        scenario_config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        2-level cascade merge for trade_simulator_config.

        Args:
            global_config: Global trade_simulator_config from scenario_set
            scenario_config: Scenario-specific trade_simulator_config

        Returns:
            Merged trade_simulator_config with scenario taking precedence
        """
        result = copy.deepcopy(global_config)

        if scenario_config:
            result = ScenarioCascade._deep_merge(result, scenario_config)

        return result

    @staticmethod
    def merge_stress_test_config(
        global_config: Dict[str, Any],
        scenario_config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        2-level cascade merge for stress_test_config.

        Args:
            global_config: Global stress_test_config from scenario_set
            scenario_config: Scenario-specific stress_test_config

        Returns:
            Merged stress_test_config with scenario taking precedence
        """
        result = copy.deepcopy(global_config)

        if scenario_config:
            result = ScenarioCascade._deep_merge(result, scenario_config)

        return result

    @staticmethod
    def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
        """
        Recursively merge override dict into base dict.

        Nested dicts are merged deeply, not replaced.
        All other values (including lists) are replaced.

        Args:
            base: Base configuration dict
            override: Override configuration dict

        Returns:
            Merged dict with override taking precedence
        """
        result = copy.deepcopy(base)

        for key, override_value in override.items():
            # If key exists in base and both are dicts → merge recursively
            if key in result and isinstance(result[key], dict) and isinstance(override_value, dict):
                result[key] = ScenarioCascade._deep_merge(
                    result[key], override_value)
            else:
                # Otherwise replace (handles primitives, lists, new keys)
                result[key] = copy.deepcopy(override_value)

        return result
