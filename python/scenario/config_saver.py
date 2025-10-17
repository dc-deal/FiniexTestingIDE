"""
FiniexTestingIDE - Scenario Config Saver
Saves TestScenario objects to JSON configuration files

REFACTORED (Worker Instance System):
- Uses worker_instances dict instead of worker_types array
- worker_instances only in global (architecture)
- workers can be overridden per scenario (parameters only)
- Proper cascade support for new structure

FIXED (C#003):
- trade_simulator_config support in save/load
- Proper override detection for all config sections
"""

from python.components.logger.bootstrap_logger import setup_logging
from pathlib import Path
from typing import List, Dict, Any
from datetime import datetime
import json

from python.framework.types.global_types import TestScenario

vLog = setup_logging(name="ScenarioConfigSaver")


class ScenarioConfigSaver:
    """
    Saves TestScenario objects to JSON config files.

    REFACTORED (Worker Instance System): Properly handles worker_instances
    and workers dict with instance names as keys.
    """

    def __init__(self, output_dir: str = "./configs/scenario_sets"):
        """
        Initialize config saver.

        Args:
            output_dir: Directory to save config files
        """
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def save_config(
        self,
        scenarios: List[TestScenario],
        output_filename: str,
        scenario_set_name: str = None
    ):
        """
        Save scenarios to JSON config file.

        REFACTORED (Worker Instance System): Uses worker_instances dict.

        Strategy:
        1. Extract global config from first scenario
        2. For each scenario, extract only overrides vs global
        3. Save in hierarchical structure with minimal redundancy

        Args:
            scenarios: List of TestScenario objects
            output_filename: Output filename (e.g., "eurusd_3_windows.json")
            scenario_set_name: Optional name (defaults to filename without .json)
        """
        if not scenarios:
            raise ValueError("No scenarios to save")

        # Default scenario set name
        if scenario_set_name is None:
            scenario_set_name = output_filename.replace(".json", "")

        # Extract global config from first scenario
        first_scenario = scenarios[0]

        # Global strategy config
        global_strategy = {
            "decision_logic_type": first_scenario.strategy_config.get("decision_logic_type"),
            "worker_instances": first_scenario.strategy_config.get("worker_instances", {}),
            "workers": first_scenario.strategy_config.get("workers", {}),
        }

        # Add decision_logic_config if present
        if "decision_logic_config" in first_scenario.strategy_config:
            global_strategy["decision_logic_config"] = first_scenario.strategy_config["decision_logic_config"]

        # Global execution config
        global_execution = first_scenario.execution_config or {}

        # Global trade simulator config
        global_trade_simulator = first_scenario.trade_simulator_config or {}

        # Build scenario list with overrides only
        scenario_list = []
        for scenario in scenarios:
            scenario_dict = self._build_scenario_dict(
                scenario=scenario,
                global_strategy=global_strategy,
                global_execution=global_execution,
                global_trade_simulator=global_trade_simulator
            )
            scenario_list.append(scenario_dict)

        # Build complete config structure
        config = {
            "version": "1.0",
            "scenario_set_name": scenario_set_name,
            "created": datetime.now().isoformat(),
            "global": {
                "data_mode": first_scenario.data_mode,  # Global data mode
                "strategy_config": global_strategy,
                "execution_config": global_execution,
                "trade_simulator_config": global_trade_simulator,
            },
            "scenarios": scenario_list
        }

        # Write to file
        output_path = self.output_dir / output_filename
        with open(output_path, "w") as f:
            json.dump(config, f, indent=2)

        vLog.info(
            f"✅ Saved {len(scenarios)} scenarios → {output_path}"
        )

    def _build_scenario_dict(
        self,
        scenario: TestScenario,
        global_strategy: Dict[str, Any],
        global_execution: Dict[str, Any],
        global_trade_simulator: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Build scenario dict with only overrides vs global.

        REFACTORED (Worker Instance System): Properly handles worker_instances
        as architecture (global only) and workers as parameters (can override).

        Args:
            scenario: TestScenario to convert
            global_strategy: Global strategy config for comparison
            global_execution: Global execution config for comparison
            global_trade_simulator: Global trade simulator config for comparison

        Returns:
            Scenario dict with minimal redundancy
        """
        # Base scenario properties (always included)
        scenario_dict = {
            "name": scenario.name,
            "symbol": scenario.symbol,
            "start_date": scenario.start_date,
            "end_date": scenario.end_date,
            "max_ticks": scenario.max_ticks,
            "data_mode": scenario.data_mode,
            "enabled": scenario.enabled,
        }

        # ============================================
        # Strategy Config Overrides
        # ============================================
        scenario_strategy_overrides = {}

        # Check for worker parameter overrides (per-parameter comparison)
        worker_overrides = self._extract_worker_overrides(
            global_workers=global_strategy.get("workers", {}),
            scenario_workers=scenario.strategy_config.get("workers", {})
        )
        if worker_overrides:
            scenario_strategy_overrides["workers"] = worker_overrides

        # Check for decision_logic_config overrides
        scenario_decision_config = scenario.strategy_config.get(
            "decision_logic_config", {})
        global_decision_config = global_strategy.get(
            "decision_logic_config", {})

        decision_config_overrides = self._extract_dict_overrides(
            global_dict=global_decision_config,
            scenario_dict=scenario_decision_config
        )
        if decision_config_overrides:
            scenario_strategy_overrides["decision_logic_config"] = decision_config_overrides

        # Validate: worker_instances should NOT be overridden
        scenario_instances = scenario.strategy_config.get(
            "worker_instances", {})
        if scenario_instances and scenario_instances != global_strategy.get("worker_instances", {}):
            vLog.warning(
                f"⚠️ Scenario '{scenario.name}' attempts to override worker_instances. "
                f"Worker architecture is global only and will be ignored!"
            )

        # Add strategy_config if there are any overrides
        if scenario_strategy_overrides:
            scenario_dict["strategy_config"] = scenario_strategy_overrides
        else:
            scenario_dict["strategy_config"] = {}

        # ============================================
        # Execution Config Overrides
        # ============================================
        execution_overrides = self._extract_dict_overrides(
            global_dict=global_execution,
            scenario_dict=scenario.execution_config or {}
        )
        if execution_overrides:
            scenario_dict["execution_config"] = execution_overrides
        else:
            scenario_dict["execution_config"] = {}

        # ============================================
        # Trade Simulator Config Overrides
        # ============================================
        trade_simulator_overrides = self._extract_dict_overrides(
            global_dict=global_trade_simulator,
            scenario_dict=scenario.trade_simulator_config or {}
        )
        if trade_simulator_overrides:
            scenario_dict["trade_simulator_config"] = trade_simulator_overrides
        else:
            scenario_dict["trade_simulator_config"] = {}

        return scenario_dict

    def _extract_worker_overrides(
        self,
        global_workers: Dict[str, Dict[str, Any]],
        scenario_workers: Dict[str, Dict[str, Any]]
    ) -> Dict[str, Dict[str, Any]]:
        """
        Extract only worker parameters that differ from global.

        This does per-parameter comparison for each worker instance.
        Only parameters that differ are included in the override.

        Example:
            Global: {"rsi_fast": {"period": 14, "timeframe": "M5"}}
            Scenario: {"rsi_fast": {"period": 5, "timeframe": "M5"}}
            Result: {"rsi_fast": {"period": 5}}  # Only the difference

        Args:
            global_workers: Global worker configurations (indexed by instance name)
            scenario_workers: Scenario worker configurations (indexed by instance name)

        Returns:
            Dict with only the overridden parameters per worker instance
        """
        overrides = {}

        for instance_name, scenario_config in scenario_workers.items():
            global_config = global_workers.get(instance_name, {})

            # Compare each parameter
            instance_overrides = {}
            for param, value in scenario_config.items():
                if global_config.get(param) != value:
                    instance_overrides[param] = value

            # Only add if there are actual overrides
            if instance_overrides:
                overrides[instance_name] = instance_overrides

        return overrides

    def _extract_dict_overrides(
        self,
        global_dict: Dict[str, Any],
        scenario_dict: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Extract only parameters that differ between global and scenario.

        Generic dict comparison for execution_config, decision_logic_config, etc.

        Args:
            global_dict: Global configuration
            scenario_dict: Scenario configuration

        Returns:
            Dict with only the overridden parameters
        """
        overrides = {}

        for key, value in scenario_dict.items():
            if global_dict.get(key) != value:
                overrides[key] = value

        return overrides

    def save_single_scenario(
        self,
        scenario: TestScenario,
        output_filename: str
    ):
        """
        Save a single scenario as a config file.

        Convenience method for saving one scenario.
        The scenario becomes both global and the only scenario in the set.

        Args:
            scenario: TestScenario to save
            output_filename: Output filename
        """
        self.save_config(
            scenarios=[scenario],
            output_filename=output_filename
        )

    def validate_config(self, config_path: str) -> bool:
        """
        Validate a saved config file.

        Checks:
        - Required fields present
        - worker_instances is dict (not array)
        - workers uses instance names as keys
        - No worker_types field (deprecated)

        Args:
            config_path: Path to config file

        Returns:
            True if valid, raises ValueError otherwise
        """
        with open(config_path, "r") as f:
            config = json.load(f)

        # Check required top-level fields
        required_fields = ["version", "global", "scenarios"]
        for field in required_fields:
            if field not in config:
                raise ValueError(f"Missing required field: {field}")

        # Check global strategy config
        global_strategy = config["global"].get("strategy_config", {})

        # Validate worker_instances (new system)
        if "worker_instances" not in global_strategy:
            raise ValueError(
                "Missing 'worker_instances' in global.strategy_config. "
                "This is required for worker instance system."
            )

        worker_instances = global_strategy["worker_instances"]
        if not isinstance(worker_instances, dict):
            raise ValueError(
                f"'worker_instances' must be a dict, got {type(worker_instances).__name__}"
            )

        # Validate workers use instance names
        workers = global_strategy.get("workers", {})
        for instance_name in workers.keys():
            if instance_name not in worker_instances:
                raise ValueError(
                    f"Worker config for '{instance_name}' has no corresponding "
                    f"entry in worker_instances"
                )

        # Reject deprecated worker_types
        if "worker_types" in global_strategy:
            raise ValueError(
                "Config contains deprecated 'worker_types' field. "
                "Use 'worker_instances' dict instead. "
                "Old config format is not supported."
            )

        # Validate scenarios
        if not config["scenarios"]:
            raise ValueError("No scenarios defined in config")

        for i, scenario in enumerate(config["scenarios"]):
            required_scenario_fields = [
                "name", "symbol", "start_date", "end_date"]
            for field in required_scenario_fields:
                if field not in scenario:
                    raise ValueError(
                        f"Scenario {i} missing required field: {field}"
                    )

        vLog.info(f"✅ Config validation passed: {config_path}")
        return True
