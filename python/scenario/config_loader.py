"""
FiniexTestingIDE - Scenario Config System
Config Loader (FIXED: Deep copy prevents config mutation)
"""

import copy  # CRITICAL: For deep copying nested structures
import json
from pathlib import Path
from typing import List, Dict, Any
from datetime import datetime
from python.framework.exceptions.configuration_errors import ScenarioSetConfigurationError
from python.framework.exceptions.data_validation_errors import InvalidDateRangeError
from python.framework.utils.parameter_override_detector import ParameterOverrideDetector
from python.configuration.app_config_manager import AppConfigManager
from python.framework.validators.scenario_validator import ScenarioValidator

from python.framework.types.scenario_set_types import LoadedScenarioConfig, ScenarioSet, SingleScenario

from python.components.logger.bootstrap_logger import get_logger
vLog = get_logger()


class ScenarioConfigLoader:
    """
    Loads test scenarios from JSON config files

    FIXED (Parameter Inheritance Bug):
    Uses deep copy to prevent config mutation across scenarios
    """

    def __init__(self, config_path: str = "./configs/scenario_sets/"):
        """
        Args:
            config_path: Directory containing scenario config files
        """
        self.config_path = Path(config_path)
        self.config_path.mkdir(parents=True, exist_ok=True)

    def load_config(self, config_file: str) -> ScenarioSet:
        """
        Load scenarios from JSON config file

        Args:
            config_file: Config filename (e.g., "eurusd_3_windows.json")

        Returns:
            List of SingleScenario objects
        """
        config_path = self.config_path / config_file

        if not config_path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")

        vLog.info(f"📂 Loading scenarios from: {config_path}")

        with open(config_path, 'r') as f:
            config = json.load(f)

        # Parse global defaults
        global_config = config.get('global', {})
        global_strategy = global_config.get('strategy_config', {})
        global_execution = global_config.get('execution_config', {})

        # Parse global trade_simulator_config
        global_trade_simulator = global_config.get(
            'trade_simulator_config', {})

        # Get warn_on_override flag from app_config
        app_config = AppConfigManager()
        warn_on_override = app_config.should_warn_on_override()

        scenarios: List[SingleScenario] = []
        disabled_count = 0

        scenario_set_name = config.get('scenario_set_name', [])
        if not scenario_set_name:
            raise ScenarioSetConfigurationError(
                file_name=config_path,
                reason="Property 'scenario_set_name' is missing in JSON root.",
                sceanrio_set_configuration=config)

        for scenario_data in config.get('scenarios', []):
            # Filters out disabled scenarios during load
            is_enabled = scenario_data.get('enabled', True)  # Default: True
            if not is_enabled:
                disabled_count += 1
                vLog.debug(
                    f"🔻 Skipping disabled scenario: {scenario_data['name']}")
                continue  # Skip disabled

            # Merge strategy config
            scenario_strategy = {**global_strategy}
            if scenario_data.get('strategy_config'):
                scenario_strategy.update(scenario_data['strategy_config'])

            # Merge execution config
            scenario_execution = {**global_execution}
            if scenario_data.get('execution_config'):
                scenario_execution.update(scenario_data['execution_config'])

            # Merge trade_simulator_config (global + scenario-specific)
            scenario_trade_simulator = {**global_trade_simulator}
            if scenario_data.get('trade_simulator_config'):
                scenario_trade_simulator.update(
                    scenario_data['trade_simulator_config'])

            # ============================================
            # NEU: PARAMETER OVERRIDE DETECTION & WARNING (COMPLETE!)
            # ============================================
            ParameterOverrideDetector.detect_and_log_overrides(
                scenario_name=scenario_data['name'],
                global_strategy=global_strategy,
                global_execution=global_execution,
                global_trade_simulator=global_trade_simulator,
                scenario_strategy=scenario_data.get('strategy_config', {}),
                scenario_execution=scenario_data.get('execution_config', {}),
                scenario_trade_simulator=scenario_data.get(
                    'trade_simulator_config', {}),
                logger=vLog,
                warn_on_override=warn_on_override
            )

            # ============================================
            # DATE VALIDATION & MODE DETECTION
            # ============================================
            self._validate_scenario_dates(scenario_data)

            scenario = SingleScenario(
                name=scenario_data['name'],
                symbol=scenario_data['symbol'],
                start_date=scenario_data['start_date'],
                end_date=scenario_data['end_date'],
                data_mode=scenario_data.get('data_mode', 'realistic'),
                max_ticks=scenario_data.get('max_ticks'),
                strategy_config=scenario_strategy,
                execution_config=scenario_execution,
                # Add trade_simulator_config to SingleScenario
                trade_simulator_config=scenario_trade_simulator if scenario_trade_simulator else None,
            )
            scenarios.append(scenario)

        if disabled_count > 0:
            vLog.debug(
                f"🔻 Filtered out {disabled_count} disabled scenario(s)")

        # === CURRENCY VALIDATION ===
        # Validate that all scenarios use same quote currency (for MVP)
        if scenarios:
            try:
                detected_currency = ScenarioValidator.get_currency_consistency(
                    scenarios)
                vLog.info(
                    f"✅ Currency validation passed: All scenarios use {detected_currency} as quote currency"
                )
            except ValueError as e:
                # Re-raise with enhanced error message
                vLog.error(f"❌ Currency validation failed:\n{str(e)}")
                raise

        vLog.info(f"✅ Loaded {len(scenarios)} scenarios from {config_file}")

        # ScenarioSet erstellt SELBST seine Logger
        return LoadedScenarioConfig(
            scenario_set_name=scenario_set_name,
            scenarios=scenarios,
            config_path=config_path
        )

    def _deep_merge_strategy_configs(
        self,
        global_config: Dict[str, Any],
        scenario_config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Deep merge strategy configs with FULL cascading support.

        FIXED: Now handles decision_logic_config properly!
        """
        # Deep copy to prevent mutation
        merged = copy.deepcopy(global_config)

        # ============================================
        # STEP 1: Handle top-level non-dict keys
        # ============================================
        for key, value in scenario_config.items():
            # Skip nested dicts - they need special handling
            if key in ['workers', 'decision_logic_config']:
                continue

            # Simple override for non-dict values
            merged[key] = value

        # ============================================
        # STEP 2: Deep merge workers dict (BESTEHT SCHON)
        # ============================================
        if 'workers' in scenario_config:
            if 'workers' not in merged:
                merged['workers'] = {}

            for worker_type, worker_params in scenario_config['workers'].items():
                if worker_type in merged['workers']:
                    merged['workers'][worker_type] = {
                        **merged['workers'][worker_type],
                        **worker_params
                    }
                else:
                    merged['workers'][worker_type] = worker_params

        # ============================================
        # STEP 3: Deep merge decision_logic_config (NEU!)
        # ============================================
        if 'decision_logic_config' in scenario_config:
            if 'decision_logic_config' not in merged:
                merged['decision_logic_config'] = {}

            # Merge decision logic parameters
            merged['decision_logic_config'].update(
                scenario_config['decision_logic_config']
            )

        return merged

    def _validate_scenario_dates(
        self,
        scenario_data: Dict[str, Any]
    ) -> None:
        """
            Validate scenario date range and log execution mode.

            NO AUTO-FIX! Just validation and mode logging.

            Cases:
            A) max_ticks set + valid dates → INFO (tick-limited mode)
            B) max_ticks null + valid dates → INFO (timespan mode)
            C) max_ticks null + invalid dates → ERROR (critical config error)
            D) max_ticks set + invalid dates → ERROR (invalid config)

            Args:
                scenario_data: Scenario dict (read-only)

            Raises:
                ValueError: If dates are invalid
            """
        start_date_str = scenario_data['start_date']
        end_date_str = scenario_data['end_date']
        max_ticks = scenario_data.get('max_ticks')
        name = scenario_data['name']

        # Parse dates for comparison
        start_dt = datetime.fromisoformat(start_date_str)
        end_dt = datetime.fromisoformat(end_date_str)

        # Check date validity
        if end_dt < start_dt:
            raise InvalidDateRangeError(
                scenario_name=name,
                start_date=start_date_str,
                end_date=end_date_str,
                max_ticks=max_ticks
            )
        else:
            # Valid dates - log execution mode
            if max_ticks:
                # Case A: max_ticks mode
                vLog.info(
                    f"ℹ️  Scenario '{name}': Tick-limited mode (max_ticks={max_ticks:,})"
                )
            else:
                # Case B: Timespan mode
                duration = end_dt - start_dt
                days = duration.days
                hours = duration.seconds // 3600
                vLog.info(
                    f"ℹ️  Scenario '{name}': Timespan mode ({days}d {hours}h)"
                )
