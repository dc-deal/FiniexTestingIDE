"""
FiniexTestingIDE - Scenario Config System
Config Loader (FIXED: Deep copy prevents config mutation)
"""

import copy  # CRITICAL: For deep copying nested structures
import json
from python.components.logger.bootstrap_logger import setup_logging
from pathlib import Path
from typing import List, Dict, Any
from datetime import datetime

from python.framework.types import TestScenario

vLog = setup_logging(name="StrategyRunner")


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

    def load_config(self, config_file: str) -> List[TestScenario]:
        """
        Load scenarios from JSON config file

        Args:
            config_file: Config filename (e.g., "eurusd_3_windows.json")

        Returns:
            List of TestScenario objects
        """
        config_path = self.config_path / config_file

        if not config_path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")

        vLog.info(f"📂 Loading scenarios from: {config_path}")

        with open(config_path, 'r') as f:
            config = json.load(f)

        scenarios = []

        # Global defaults (separate strategy + execution)
        global_strategy = config.get("global", {}).get("strategy_config", {})
        global_execution = config.get("global", {}).get("execution_config", {})

        # Load each scenario
        for scenario_config in config.get("scenarios", []):
            # ============================================
            # FIXED (Issue 2 - Parameter Inheritance):
            # Deep merge strategy config (handles nested workers dict)
            # ============================================
            scenario_strategy = scenario_config.get("strategy_config", {})

            # Deep merge strategy config (handles nested workers dict)
            strategy_config = self._deep_merge_strategy_configs(
                global_strategy,
                scenario_strategy
            )

            # Execution config stays shallow (no nested structures)
            execution_config = {**global_execution, **
                                scenario_config.get("execution_config", {})}

            scenario = TestScenario(
                symbol=scenario_config["symbol"],
                start_date=scenario_config["start_date"],
                end_date=scenario_config["end_date"],
                max_ticks=scenario_config.get("max_ticks", 1000),
                data_mode=scenario_config.get("data_mode", "realistic"),

                # Strategy-Logic
                strategy_config=strategy_config,

                # Execution-Optimization
                execution_config=execution_config if execution_config else None,

                name=scenario_config.get(
                    "name", f"{scenario_config['symbol']}_test")
            )
            scenarios.append(scenario)

        vLog.debug(f"✅ Loaded {len(scenarios)} scenarios")
        return scenarios

    def _deep_merge_strategy_configs(
        self,
        global_config: Dict[str, Any],
        scenario_config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Deep merge strategy configs with intelligent workers dict handling.

        CRITICAL BUG FIX:
        The original implementation used shallow copy (global_config.copy())
        which caused a nasty bug: When Scenario 1 merged its worker overrides,
        it MUTATED the original global_config because nested dicts were only
        referenced, not copied!

        This caused Scenario 2 and 3 to inherit Scenario 1's overrides even
        though they shouldn't have!

        Example of the bug:
            Global:   {"workers": {"CORE/rsi": {"period": 14, "timeframe": "M5"}}}
            Scenario 1 override: {"workers": {"CORE/rsi": {"period": 5, "timeframe": "M1"}}}

            BUG: After Scenario 1 loads, global_config is MUTATED to:
            {"workers": {"CORE/rsi": {"period": 5, "timeframe": "M1"}}}

            So Scenario 2 (which should get M5) gets M1 instead!

        Solution:
            Use copy.deepcopy() instead of .copy() to prevent mutation.

        Args:
            global_config: Global strategy config (will NOT be mutated)
            scenario_config: Scenario-specific overrides

        Returns:
            Merged strategy config with intelligent workers inheritance
        """
        # ============================================
        # CRITICAL FIX: Deep copy to prevent mutation
        # ============================================
        # This creates a completely independent copy of global_config
        # including all nested dictionaries. Now Scenario 1's changes
        # cannot affect Scenario 2 and 3!
        merged = copy.deepcopy(global_config)  # NOT global_config.copy()!

        # Merge top-level keys (shallow merge for most keys)
        for key, value in scenario_config.items():
            if key != 'workers':  # Workers needs special handling
                merged[key] = value

        # ============================================
        # Special handling: Merge workers dict per-worker
        # ============================================
        if 'workers' in scenario_config:
            if 'workers' not in merged:
                merged['workers'] = {}

            # Merge each worker's parameters individually
            for worker_type, worker_params in scenario_config['workers'].items():
                if worker_type in merged['workers']:
                    # Merge parameters for existing worker (global + scenario overrides)
                    merged['workers'][worker_type] = {
                        **merged['workers'][worker_type],  # Global base params
                        **worker_params  # Scenario overrides
                    }
                else:
                    # New worker only in scenario config
                    merged['workers'][worker_type] = worker_params

        return merged

    def save_config(self, scenarios: List[TestScenario], config_file: str):
        """
        Save scenarios to JSON config file

        Args:
            scenarios: List of TestScenario objects
            config_file: Output filename
        """
        config_path = self.config_path / config_file

        # ============================================
        # FIXED (Issue 2): Extract global strategy config from first scenario
        # Only worker parameter overrides go into scenario-level configs
        # ============================================

        # Extract global strategy config from first scenario (if available)
        if scenarios and scenarios[0].strategy_config:
            first_strategy = scenarios[0].strategy_config

            # Global strategy config contains the strategic decisions
            global_strategy = {
                "decision_logic_type": first_strategy.get("decision_logic_type"),
                "worker_types": first_strategy.get("worker_types", []),
                "workers": first_strategy.get("workers", {}),
            }

            # Add decision_logic_config if present
            if "decision_logic_config" in first_strategy:
                global_strategy["decision_logic_config"] = first_strategy["decision_logic_config"]
        else:
            # Fallback to legacy defaults (should not happen with new structure)
            global_strategy = {
                "rsi_period": 14,
                "envelope_period": 20,
                "envelope_deviation": 0.02,
            }

        # Default execution config
        default_execution = {
            "parallel_workers": True,
            "worker_parallel_threshold_ms": 1.0,
            "max_parallel_scenarios": 4,
            "adaptive_parallelization": True,
            "log_performance_stats": True,
        }

        config = {
            "version": "1.0",
            "scenario_set_name": "scn_"+config_file.replace('.json', ''),
            "created": datetime.now().isoformat(),
            "global": {
                "data_mode": "realistic",
                "strategy_config": global_strategy,  # Full strategy config in global
                "execution_config": default_execution,
            },
            "scenarios": []
        }

        for scenario in scenarios:
            # ============================================
            # CRITICAL: Only save worker parameter overrides per scenario
            # Not the entire strategy_config!
            # ============================================
            scenario_strategy_override = {}

            # Check if this scenario has worker parameter overrides
            if scenario.strategy_config and "workers" in scenario.strategy_config:
                scenario_workers = scenario.strategy_config["workers"]
                global_workers = global_strategy.get("workers", {})

                # Only include workers that differ from global config
                worker_overrides = {}
                for worker_type, worker_params in scenario_workers.items():
                    global_params = global_workers.get(worker_type, {})

                    # Find parameters that differ from global
                    param_overrides = {}
                    for param_name, param_value in worker_params.items():
                        if global_params.get(param_name) != param_value:
                            param_overrides[param_name] = param_value

                    # Only add this worker if it has overrides
                    if param_overrides:
                        worker_overrides[worker_type] = param_overrides

                # Only set workers key if there are actual overrides
                if worker_overrides:
                    scenario_strategy_override["workers"] = worker_overrides

            scenario_dict = {
                "name": scenario.name,
                "symbol": scenario.symbol,
                "start_date": scenario.start_date,
                "end_date": scenario.end_date,
                "max_ticks": scenario.max_ticks,
                "data_mode": scenario.data_mode,

                # Only worker overrides (or empty dict if none)
                "strategy_config": scenario_strategy_override,

                # Execution overrides (only if different from global)
                "execution_config": scenario.execution_config if scenario.execution_config != default_execution else {},
            }
            config["scenarios"].append(scenario_dict)

        with open(config_path, 'w') as f:
            json.dump(config, f, indent=2)

        vLog.info(f"💾 Saved {len(scenarios)} scenarios to: {config_path}")
