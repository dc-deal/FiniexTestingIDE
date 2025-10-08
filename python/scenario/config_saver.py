"""
FiniexTestingIDE - Scenario Config System
Config Saver (FIXED: Deep copy prevents config mutation)
"""

import copy  # CRITICAL: For deep copying nested structures
import json
from python.components.logger.bootstrap_logger import setup_logging
from pathlib import Path
from typing import List, Dict, Any
from datetime import datetime
from python.framework.utils.parameter_override_detector import ParameterOverrideDetector
from python.configuration.app_config_loader import AppConfigLoader

from python.framework.types import TestScenario

vLog = setup_logging(name="StrategyRunner")


class ScenarioConfigSaver:
    """
    Saves test scenarios from JSON config files

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

    def save_config(self, scenarios: List[TestScenario], config_file: str):
        """
        Save scenarios to JSON config file

        Args:
            scenarios: List of TestScenario objects
            config_file: Output filename
        """
        config_path = self.config_path / config_file

        # ============================================
        # Extract global strategy config from first scenario
        # ============================================
        if scenarios and scenarios[0].strategy_config:
            first_strategy = scenarios[0].strategy_config

            global_strategy = {
                "decision_logic_type": first_strategy.get("decision_logic_type"),
                "worker_types": first_strategy.get("worker_types", []),
                "workers": first_strategy.get("workers", {}),
            }

            if "decision_logic_config" in first_strategy:
                global_strategy["decision_logic_config"] = first_strategy["decision_logic_config"]
        else:
            # Fallback
            global_strategy = {
                "decision_logic_type": "CORE/simple_consensus",
                "worker_types": ["CORE/rsi", "CORE/envelope"],
                "workers": {},
            }

        # ============================================
        # Extract global execution config from first scenario
        # ============================================
        default_execution = {
            "parallel_workers": True,
            "worker_parallel_threshold_ms": 1.0,
            "adaptive_parallelization": True,
            "log_performance_stats": True,
        }

        # Extract from first scenario (like strategy_config!)
        global_execution = default_execution.copy()
        if scenarios and scenarios[0].execution_config:
            global_execution.update(scenarios[0].execution_config)

        # ============================================
        # Extract global trade_simulator_config
        # ============================================
        global_trade_simulator = {}
        if scenarios and scenarios[0].trade_simulator_config:
            global_trade_simulator = scenarios[0].trade_simulator_config.copy()

        # ============================================
        # Build config structure
        # ============================================
        config = {
            "version": "1.0",
            "scenario_set_name": "scn_" + config_file.replace('.json', ''),
            "created": datetime.now().isoformat(),
            "trade_simulator_seeds": {
                "api_latency_seed": 42,
                "market_execution_seed": 123
            },
            "global": {
                "data_mode": "realistic",
                "strategy_config": global_strategy,
                "execution_config": global_execution,  # ← Now correctly extracted!
                "trade_simulator_config": global_trade_simulator,
            },
            "scenarios": []
        }

        # ============================================
        # Extract overrides per scenario
        # ============================================
        for scenario in scenarios:
            # Strategy config overrides (workers + decision_logic_config)
            scenario_strategy_override = {}
            if scenario.strategy_config:
                scenario_strategy_override = ParameterOverrideDetector.extract_overrides_for_save(
                    global_config=global_strategy,
                    scenario_config=scenario.strategy_config,
                    sections=['workers', 'decision_logic_config']
                )

            # Execution config overrides
            exec_override = {}
            if scenario.execution_config:
                exec_override = ParameterOverrideDetector.extract_overrides_for_save(
                    # ✅ NOW EXISTS!
                    global_config={'execution_config': global_execution},
                    scenario_config={
                        'execution_config': scenario.execution_config},
                    sections=['execution_config']
                ).get('execution_config', {})

            # Trade simulator config overrides
            ts_override = {}
            if scenario.trade_simulator_config:
                ts_override = ParameterOverrideDetector.extract_overrides_for_save(
                    global_config={
                        'trade_simulator_config': global_trade_simulator},
                    scenario_config={
                        'trade_simulator_config': scenario.trade_simulator_config},
                    sections=['trade_simulator_config']
                ).get('trade_simulator_config', {})

            scenario_dict = {
                "name": scenario.name,
                "symbol": scenario.symbol,
                "start_date": scenario.start_date,
                "end_date": scenario.end_date,
                "max_ticks": scenario.max_ticks,
                "data_mode": scenario.data_mode,
                "enabled": scenario.enabled if not scenario.enabled else True,
                "strategy_config": scenario_strategy_override,
                "execution_config": exec_override,
                "trade_simulator_config": ts_override,
            }
            config["scenarios"].append(scenario_dict)

        with open(config_path, 'w') as f:
            json.dump(config, f, indent=2)

        vLog.info(f"💾 Saved {len(scenarios)} scenarios to: {config_path}")
