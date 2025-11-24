# ============================================
# python/framework/utils/parameter_override_detector.py
# ============================================
"""
FiniexTestingIDE - Parameter Override Detector ()
Central config management for cascade detection and override extraction

 (V0.8):
- ✅ detect_overrides() - Find differences between global/scenario
- ✅ format_overrides_for_display() - Pretty print for logs
- ✅ extract_overrides_for_save() - NEW! Get only overrides for JSON save
- ✅ Supports all config sections: workers, decision_logic_config, execution_config, trade_simulator_config

Usage:
    # In scenario_config_loader.py load_config():
    overrides = ParameterOverrideDetector.detect_overrides(global, scenario)
    formatted = ParameterOverrideDetector.format_overrides_for_display(overrides)
    
    # In scenario_config_loader.py save_config():
    strategy_overrides = ParameterOverrideDetector.extract_overrides_for_save(
        global_strategy, scenario.strategy_config, 
        sections=['workers', 'decision_logic_config']
    )
"""

from typing import Dict, Any, List, Tuple, Optional


class ParameterOverrideDetector:
    """
    Central parameter override detection and extraction.

    Provides three key functions:
    1. detect_overrides() - Find all differences (for warnings)
    2. format_overrides_for_display() - Pretty print
    3. extract_overrides_for_save() - Get only overrides (for save_config)

    This centralizes all override logic that was scattered across scenario_config_loader.
    """

    # ============================================
    # PUBLIC API: Override Detection (for warnings)
    # ============================================

    @staticmethod
    def detect_overrides(
        global_config: Dict[str, Any],
        scenario_config: Dict[str, Any],
        path_prefix: str = ""
    ) -> List[Tuple[str, Any, Any]]:
        """
        Recursively detect parameter overrides between global and scenario configs.

        Used in load_config() to warn about parameter changes.

        Args:
            global_config: Global configuration dict
            scenario_config: Scenario-specific configuration dict
            path_prefix: Current path in nested dict (for display)

        Returns:
            List of tuples: (parameter_path, global_value, scenario_value)

        Example:
            overrides = detect_overrides(
                {"workers": {"CORE/rsi": {"period": 14}}},
                {"workers": {"CORE/rsi": {"period": 5}}}
            )
            # Returns: [("workers.CORE/rsi.period", 14, 5)]
        """
        overrides = []

        # Check all config sections
        for section_key in ['strategy_config', 'execution_config', 'trade_simulator_config']:
            if section_key in scenario_config and scenario_config[section_key]:
                section_overrides = ParameterOverrideDetector._check_dict_overrides(
                    global_config.get(section_key, {}),
                    scenario_config[section_key],
                    section_key
                )
                overrides.extend(section_overrides)

        return overrides

    @staticmethod
    def format_overrides_for_display(
        overrides: List[Tuple[str, Any, Any]]
    ) -> Dict[str, str]:
        """
        Format overrides for log display.

        Converts raw override tuples into human-readable strings.

        Args:
            overrides: List of override tuples from detect_overrides()

        Returns:
            Dict mapping parameter paths to formatted strings

        Example:
            formatted = format_overrides_for_display([
                ("workers.CORE/rsi.period", 14, 5)
            ])
            # Returns: {"workers.CORE/rsi.period": "14 → 5"}
        """
        formatted = {}

        for path, global_val, scenario_val in overrides:
            if global_val is None:
                formatted[path] = f"{scenario_val} (NEW)"
            else:
                formatted[path] = f"{global_val} → {scenario_val}"

        return formatted

    # ============================================
    # PUBLIC API: Override Extraction (for saving)
    # ============================================

    @staticmethod
    def extract_overrides_for_save(
        global_config: Dict[str, Any],
        scenario_config: Dict[str, Any],
        sections: List[str]
    ) -> Dict[str, Any]:
        """
        Extract ONLY the overrides from scenario config for saving.

        This is the KEY function that replaces all manual override loops
        in save_config(). It returns a dict containing ONLY parameters
        that differ from global config.

        Args:
            global_config: Global configuration dict
            scenario_config: Scenario-specific configuration dict
            sections: List of section keys to check (e.g., ['workers', 'decision_logic_config'])

        Returns:
            Dict with ONLY overridden parameters

        Example:
            # Global: {"workers": {"CORE/rsi": {"period": 14, "timeframe": "M5"}}}
            # Scenario: {"workers": {"CORE/rsi": {"period": 5, "timeframe": "M5"}}}

            overrides = extract_overrides_for_save(
                global_config={'workers': {'CORE/rsi': {'period': 14, 'timeframe': 'M5'}}},
                scenario_config={'workers': {'CORE/rsi': {'period': 5, 'timeframe': 'M5'}}},
                sections=['workers']
            )
            # Returns: {'workers': {'CORE/rsi': {'period': 5}}}  # Only period changed!
        """
        result = {}

        for section in sections:
            if section not in scenario_config or not scenario_config[section]:
                continue

            global_section = global_config.get(section, {})
            scenario_section = scenario_config[section]

            # Handle nested dicts (like workers)
            if isinstance(scenario_section, dict):
                section_overrides = ParameterOverrideDetector._extract_dict_overrides(
                    global_section,
                    scenario_section
                )
                if section_overrides:
                    result[section] = section_overrides
            # Handle simple values (shouldn't happen in our use case, but for completeness)
            elif scenario_section != global_section:
                result[section] = scenario_section

        return result

    # ============================================
    # PRIVATE HELPERS
    # ============================================

    @staticmethod
    def _check_dict_overrides(
        global_dict: Dict[str, Any],
        scenario_dict: Dict[str, Any],
        path: str
    ) -> List[Tuple[str, Any, Any]]:
        """
        Recursively check for overrides in nested dictionaries.

        Used by detect_overrides() for warning generation.

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
                # parameter in scenario (not in global)
                overrides.append((full_path, None, scenario_value))
                continue

            global_value = global_dict[key]

            # Handle nested dicts (e.g., workers config, decision_logic_config)
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
    def _extract_dict_overrides(
        global_dict: Dict[str, Any],
        scenario_dict: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Extract ONLY overridden parameters from nested dicts.

        Used by extract_overrides_for_save() for save_config().

        This is the workhorse function that replaces all manual
        for loops in save_config().

        Args:
            global_dict: Global dict values
            scenario_dict: Scenario dict values

        Returns:
            Dict containing ONLY overridden values

        Example:
            global_dict = {
                "CORE/rsi": {"period": 14, "timeframe": "M5"},
                "CORE/envelope": {"period": 20}
            }
            scenario_dict = {
                "CORE/rsi": {"period": 5, "timeframe": "M5"},
                "CORE/envelope": {"period": 20}
            }

            result = _extract_dict_overrides(global_dict, scenario_dict)
            # Returns: {"CORE/rsi": {"period": 5}}
            # Note: CORE/envelope is completely omitted (no changes)
            # Note: timeframe is omitted (same as global)
        """
        result = {}

        for key, scenario_value in scenario_dict.items():
            global_value = global_dict.get(key)

            # Handle nested dicts (e.g., worker parameters)
            if isinstance(scenario_value, dict) and isinstance(global_value, dict):
                # Recursively extract overrides from nested dict
                nested_overrides = {}
                for param_key, param_value in scenario_value.items():
                    global_param_value = global_value.get(param_key)

                    # Only include if different from global
                    if param_value != global_param_value:
                        nested_overrides[param_key] = param_value

                # Only include this key if it has overrides
                if nested_overrides:
                    result[key] = nested_overrides

            # Handle simple values
            elif scenario_value != global_value:
                result[key] = scenario_value

        return result

    @staticmethod
    def detect_and_log_overrides(
        scenario_name: str,
        global_strategy: Dict[str, Any],
        global_execution: Dict[str, Any],
        global_trade_simulator: Dict[str, Any],
        scenario_strategy: Dict[str, Any],
        scenario_execution: Dict[str, Any],
        scenario_trade_simulator: Dict[str, Any],
        logger,
        warn_on_override: bool = True
    ) -> List[Tuple[str, Any, Any]]:
        """
        Detect and log all parameter overrides for a scenario.

        This is a convenience function that:
        1. Checks all config sections (strategy, execution, trade_simulator)
        2. Collects all overrides
        3. Logs warnings if enabled
        4. Returns all overrides for further processing

        Use this in load_config() to centralize override detection.

        Args:
            scenario_name: Name of the scenario (for logging)
            global_strategy: Global strategy_config
            global_execution: Global execution_config
            global_trade_simulator: Global trade_simulator_config
            scenario_strategy: Scenario's strategy_config (may be empty)
            scenario_execution: Scenario's execution_config (may be empty)
            scenario_trade_simulator: Scenario's trade_simulator_config (may be empty)
            logger: Logger instance (e.g., vLog)
            warn_on_override: Whether to log warnings (from app_config)

        Returns:
            List of all override tuples (for further processing if needed)

        Example:
            overrides = ParameterOverrideDetector.detect_and_log_overrides(
                scenario_name="EURUSD_window_02",
                global_strategy=global_strategy,
                global_execution=global_execution,
                global_trade_simulator=global_trade_simulator,
                scenario_strategy=scenario_data.get('strategy_config', {}),
                scenario_execution=scenario_data.get('execution_config', {}),
                scenario_trade_simulator=scenario_data.get('trade_simulator_config', {}),
                logger=vLog,
                warn_on_override=True
            )
        """
        if not warn_on_override:
            return []

        all_overrides = []

        # Check strategy_config overrides
        if scenario_strategy:
            strategy_overrides = ParameterOverrideDetector.detect_overrides(
                {'strategy_config': global_strategy},
                {'strategy_config': scenario_strategy}
            )
            all_overrides.extend(strategy_overrides)

        # Check execution_config overrides
        if scenario_execution:
            exec_overrides = ParameterOverrideDetector.detect_overrides(
                {'execution_config': global_execution},
                {'execution_config': scenario_execution}
            )
            all_overrides.extend(exec_overrides)

        # Check trade_simulator_config overrides
        if scenario_trade_simulator:
            ts_overrides = ParameterOverrideDetector.detect_overrides(
                {'trade_simulator_config': global_trade_simulator},
                {'trade_simulator_config': scenario_trade_simulator}
            )
            all_overrides.extend(ts_overrides)

        # Log all overrides together
        if all_overrides:
            formatted = ParameterOverrideDetector.format_overrides_for_display(
                all_overrides)
            logger.warning(
                f"⚠️  Parameter overrides in scenario '{scenario_name}':")
            for path, change in formatted.items():
                logger.warning(f"   └─ {path}: {change}")

        return all_overrides
