"""
FiniexTestingIDE - Scenario Config System
Config Loader (FIXED: Deep copy prevents config mutation)
"""

import copy  # CRITICAL: For deep copying nested structures
import json
from pathlib import Path
from typing import List, Dict, Any
from python.framework.utils.parameter_override_detector import ParameterOverrideDetector
from python.configuration.app_config_manager import AppConfigManager

from python.framework.types.scenario_set_types import LoadedScenarioConfig, ScenarioSet, SingleScenario

from python.framework.logging.bootstrap_logger import get_global_logger
from python.framework.utils.time_utils import parse_datetime
from python.scenario.scenario_cascade import ScenarioCascade
vLog = get_global_logger()


class ScenarioConfigLoader:
    """
    Loads test scenarios from JSON config files

    FIXED (Parameter Inheritance Bug):
    Uses deep copy to prevent config mutation across scenarios
    """

    def __init__(self):
        """
        Initialize loader with paths from AppConfigManager.
        """
        app_config = AppConfigManager()
        self.config_path = Path(app_config.get_scenario_sets_path())
        self.config_path.mkdir(parents=True, exist_ok=True)

    def load_config(self, config_file: str) -> LoadedScenarioConfig:
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

        # Parse global stress_test_config
        global_stress_test = global_config.get('stress_test_config', {})

        # Get warn_on_override flag from app_config
        app_config = AppConfigManager()
        warn_on_override = app_config.should_warn_on_override()

        scenarios: List[SingleScenario] = []
        disabled_count = 0

        scenario_set_name = config.get('scenario_set_name', "unknown")

        current_scenario_index = 0
        for scenario_data in config.get('scenarios', []):
            # Filters out disabled scenarios during load
            is_enabled = scenario_data.get('enabled', True)  # Default: True
            if not is_enabled:
                disabled_count += 1
                vLog.debug(
                    f"🔻 Skipping disabled scenario: {scenario_data['name']}")
                continue  # Skip disabled

            # Get app-level execution defaults (3-level cascade base)
            app_execution_defaults = app_config.get_scenario_execution_defaults()

            # Deep merge strategy config (2-level: global → scenario)
            scenario_strategy = ScenarioCascade.merge_strategy_config(
                global_strategy,
                scenario_data.get('strategy_config', {})
            )

            # Deep merge execution config (3-level: app → global → scenario)
            scenario_execution = ScenarioCascade.merge_execution_config(
                app_execution_defaults,
                global_execution,
                scenario_data.get('execution_config', {})
            )

            # Deep merge trade_simulator_config (2-level: global → scenario)
            scenario_trade_simulator = ScenarioCascade.merge_trade_simulator_config(
                global_trade_simulator,
                scenario_data.get('trade_simulator_config', {})
            )

            # Deep merge stress_test_config (2-level: global → scenario)
            scenario_stress_test = ScenarioCascade.merge_stress_test_config(
                global_stress_test,
                scenario_data.get('stress_test_config', {})
            )

            # ============================================
            # PARAMETER OVERRIDE DETECTION & WARNING (COMPLETE!)
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
            # DATA SOURCE VALIDATION (REQUIRED)
            # ============================================
            # data_broker_type determines which tick/bar index to load from
            data_broker_type = scenario_data.get('data_broker_type')
            if not data_broker_type:
                raise ValueError(
                    f"Scenario '{scenario_data['name']}' missing required field 'data_broker_type'.\n"
                    f"\n"
                    f"This field specifies which data collection to load ticks/bars from.\n"
                    f"\n"
                    f"Add to your scenario:\n"
                    f"  {{\n"
                    f"    \"name\": \"{scenario_data['name']}\",\n"
                    f"    \"data_broker_type\": \"mt5\",  <-- ADD THIS\n"
                    f"    \"symbol\": \"{scenario_data.get('symbol', 'SYMBOL')}\",\n"
                    f"    ...\n"
                    f"  }}\n"
                    f"\n"
                    f"Available values depend on your imported data:\n"
                    f"  - \"mt5\" for MT5/MetaTrader data\n"
                    f"  - \"kraken_spot\" for Kraken crypto data\n"
                )

            scenario = SingleScenario(
                name=scenario_data['name'],
                # important for data packages in parallel processing -> sub processes.
                scenario_index=current_scenario_index,
                symbol=scenario_data['symbol'],
                data_broker_type=data_broker_type,
                start_date=parse_datetime(scenario_data['start_date']),
                end_date=parse_datetime(scenario_data['end_date']) if scenario_data.get(
                    'end_date') else None,
                data_mode=scenario_data.get('data_mode', 'realistic'),
                max_ticks=scenario_data.get('max_ticks'),
                strategy_config=scenario_strategy,
                execution_config=scenario_execution,
                # Add trade_simulator_config to SingleScenario
                trade_simulator_config=scenario_trade_simulator if scenario_trade_simulator else None,
                # Add stress_test_config to SingleScenario
                stress_test_config=scenario_stress_test if scenario_stress_test else None,
            )
            scenarios.append(scenario)
            current_scenario_index += 1

        if disabled_count > 0:
            vLog.debug(
                f"🔻 Filtered out {disabled_count} disabled scenario(s)")

        vLog.info(f"✅ Loaded {len(scenarios)} scenarios from {config_file}")

        # ScenarioSet erstellt SELBST seine Logger
        return LoadedScenarioConfig(
            scenario_set_name=scenario_set_name,
            scenarios=scenarios,
            config_path=config_path
        )
