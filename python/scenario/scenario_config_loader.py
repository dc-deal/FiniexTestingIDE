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

from python.framework.types.scenario_types.generator_profile_types import GeneratorProfile
from python.framework.types.scenario_types.scenario_set_types import LoadedScenarioConfig, ScenarioSet, SingleScenario

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
        self._user_config_path = Path(app_config.get_user_scenario_sets_path())
        self._user_config_path.mkdir(parents=True, exist_ok=True)
        self._user_algo_dirs = [Path(d) for d in app_config.get_user_algo_dirs()]

    def _resolve_path(self, filename: str) -> Path:
        """
        Resolve scenario set file path.

        If filename is a valid existing path (absolute or project-root-relative),
        it is used directly. Otherwise searches by filename:
        user_configs → user_algo_dirs (recursive) → configs.

        Args:
            filename: Full path or config filename (e.g., "eurusd_3_windows.json")

        Returns:
            Resolved Path
        """
        direct = Path(filename)
        if direct.exists():
            return direct

        user_path = self._user_config_path / filename
        if user_path.exists():
            return user_path

        for algo_dir in self._user_algo_dirs:
            if not algo_dir.exists():
                continue
            for p in algo_dir.rglob(filename):
                return p

        return self.config_path / filename

    def load_config(self, config_file: str) -> LoadedScenarioConfig:
        """
        Load scenarios from JSON config file

        Args:
            config_file: Config filename (e.g., "eurusd_3_windows.json")

        Returns:
            List of SingleScenario objects
        """
        config_path = self._resolve_path(config_file)

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

        # Parse global order_guard config
        global_order_guard = global_config.get('order_guard', {})

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

            # Deep merge trade_simulator_config (3-level: app → global → scenario)
            app_trade_simulator_defaults = app_config.get_trade_simulator_defaults()
            scenario_trade_simulator = ScenarioCascade.merge_trade_simulator_config(
                app_trade_simulator_defaults,
                global_trade_simulator,
                scenario_data.get('trade_simulator_config', {})
            )

            # Deep merge stress_test_config (2-level: global → scenario)
            scenario_stress_test = ScenarioCascade.merge_stress_test_config(
                global_stress_test,
                scenario_data.get('stress_test_config', {})
            )

            # Deep merge order_guard config (2-level: global → scenario)
            scenario_order_guard = ScenarioCascade.merge_order_guard_config(
                global_order_guard,
                scenario_data.get('order_guard', {})
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
                # Add order_guard_config to SingleScenario
                order_guard_config=scenario_order_guard if scenario_order_guard else None,
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

    def load_from_profiles(
        self,
        profiles: List[GeneratorProfile],
        scenario_set_json: str
    ) -> LoadedScenarioConfig:
        """
        Create LoadedScenarioConfig from one or more GeneratorProfiles.

        Loads global config (strategy, execution, trade_simulator) from
        the scenario set JSON, then creates one SingleScenario per profile
        block across all profiles with globally unique scenario indices
        and unique scenario names.

        Args:
            profiles: List of GeneratorProfiles with block definitions
            scenario_set_json: Scenario set config filename for global config

        Returns:
            LoadedScenarioConfig with merged profile-based scenarios
        """
        config_path = self._resolve_path(scenario_set_json)

        if not config_path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")

        with open(config_path, 'r') as f:
            config = json.load(f)

        # Parse global defaults from scenario set
        global_config = config.get('global', {})
        global_strategy = global_config.get('strategy_config', {})
        global_execution = global_config.get('execution_config', {})
        global_trade_simulator = global_config.get('trade_simulator_config', {})
        global_stress_test = global_config.get('stress_test_config', {})
        global_order_guard = global_config.get('order_guard', {})

        # App-level defaults for 3-tier cascade
        app_config = AppConfigManager()
        app_trade_simulator_defaults = app_config.get_trade_simulator_defaults()

        # Merge trade_simulator_config: app_defaults → global (no per-scenario in profile mode)
        merged_trade_simulator = ScenarioCascade.merge_trade_simulator_config(
            app_trade_simulator_defaults,
            global_trade_simulator,
            {}  # No per-scenario overrides in profile mode
        )

        scenario_set_name = config.get('scenario_set_name', 'unknown')

        scenarios: List[SingleScenario] = []
        global_index = 0

        for profile in profiles:
            meta = profile.profile_meta
            mode_short = 'vol' if meta.generator_mode == 'volatility_split' else 'cont'

            for block in profile.blocks:
                name = f"{meta.symbol}_{mode_short}_{block.block_index:02d}"

                scenario = SingleScenario(
                    name=name,
                    scenario_index=global_index,
                    symbol=meta.symbol,
                    data_broker_type=meta.broker_type,
                    start_date=block.start_time,
                    end_date=block.end_time,
                    data_mode='realistic',
                    max_ticks=None,
                    strategy_config=copy.deepcopy(global_strategy),
                    execution_config=copy.deepcopy(global_execution),
                    trade_simulator_config=copy.deepcopy(merged_trade_simulator) if merged_trade_simulator else None,
                    stress_test_config=copy.deepcopy(global_stress_test) if global_stress_test else None,
                    order_guard_config=copy.deepcopy(global_order_guard) if global_order_guard else None,
                    is_profile_run=True,
                )
                scenarios.append(scenario)
                global_index += 1

            vLog.info(
                f"✅ Loaded {meta.block_count} blocks from profile "
                f"({mode_short}, {meta.symbol})"
            )

        vLog.info(
            f"✅ Created {len(scenarios)} scenarios from "
            f"{len(profiles)} profile(s)"
        )

        return LoadedScenarioConfig(
            scenario_set_name=scenario_set_name,
            scenarios=scenarios,
            config_path=config_path,
            generator_profiles=profiles,
        )
