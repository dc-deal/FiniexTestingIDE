"""
FiniexTestingIDE - Parameter Override Detector
Detects when scenarios override global parameters

NEW (V0.7): Helps identify configuration conflicts
"""

from typing import Dict, Any, List, Tuple


class ParameterOverrideDetector:
    """
    Detects parameter overrides between global and scenario-level configs.

    This is useful for debugging when scenarios behave unexpectedly due to
    parameter overrides that differ from global defaults.
    """

    @staticmethod
    def detect_overrides(
        global_config: Dict[str, Any],
        scenario_config: Dict[str, Any],
        path_prefix: str = ""
    ) -> List[Tuple[str, Any, Any]]:
        """
        Recursively detect parameter overrides.

        Args:
            global_config: Global configuration dict
            scenario_config: Scenario-specific configuration dict
            path_prefix: Current path in nested dict (for display)

        Returns:
            List of tuples: (parameter_path, global_value, scenario_value)
        """
        overrides = []

        # Check execution_config overrides
        if 'execution_config' in scenario_config and scenario_config['execution_config']:
            exec_overrides = ParameterOverrideDetector._check_dict_overrides(
                global_config.get('execution_config', {}),
                scenario_config['execution_config'],
                "execution_config"
            )
            overrides.extend(exec_overrides)

        # Check strategy_config overrides (especially workers)
        if 'strategy_config' in scenario_config and scenario_config['strategy_config']:
            strategy_overrides = ParameterOverrideDetector._check_dict_overrides(
                global_config.get('strategy_config', {}),
                scenario_config['strategy_config'],
                "strategy_config"
            )
            overrides.extend(strategy_overrides)

        return overrides

    @staticmethod
    def _check_dict_overrides(
        global_dict: Dict[str, Any],
        scenario_dict: Dict[str, Any],
        path: str
    ) -> List[Tuple[str, Any, Any]]:
        """
        Check for overrides in nested dictionaries.

        Args:
            global_dict: Global dict values
            scenario_dict: Scenario dict values
            path: Current path for display

        Returns:
            List of override tuples
        """
        overrides = []

        for key, scenario_value in scenario_dict.items():
            full_path = f"{path}.{key}"

            if key not in global_dict:
                # New parameter in scenario (not in global)
                overrides.append((full_path, None, scenario_value))
                continue

            global_value = global_dict[key]

            # Handle nested dicts (e.g., workers config)
            if isinstance(scenario_value, dict) and isinstance(global_value, dict):
                nested_overrides = ParameterOverrideDetector._check_dict_overrides(
                    global_value,
                    scenario_value,
                    full_path
                )
                overrides.extend(nested_overrides)
            # Simple value comparison
            elif scenario_value != global_value:
                overrides.append((full_path, global_value, scenario_value))

        return overrides

    @staticmethod
    def format_overrides_for_display(
        overrides: List[Tuple[str, Any, Any]]
    ) -> Dict[str, str]:
        """
        Format overrides for display.

        Args:
            overrides: List of override tuples

        Returns:
            Dict mapping parameter paths to formatted strings
        """
        formatted = {}

        for path, global_val, scenario_val in overrides:
            if global_val is None:
                formatted[path] = f"{scenario_val} (NEW)"
            else:
                formatted[path] = f"{global_val} → {scenario_val}"

        return formatted
