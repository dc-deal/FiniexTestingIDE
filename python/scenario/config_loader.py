"""
FiniexTestingIDE - Scenario Config System
Config Loader
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

from python.data_worker.data_loader.core import TickDataLoader
from python.framework.types import TestScenario

logger = logging.getLogger(__name__)


class ScenarioConfigLoader:
    """
    Loads test scenarios from JSON config files
    """

    def __init__(self, config_path: str = "./configs/scenarios/"):
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
            config_file: Config filename (e.g., "heavy_workers_test.json")

        Returns:
            List of TestScenario objects
        """
        config_path = self.config_path / config_file

        if not config_path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")

        logger.info(f"Loading scenarios from: {config_path}")

        with open(config_path, 'r') as f:
            config = json.load(f)

        scenarios = []

        # Global defaults
        global_config = config.get("global", {})

        # Load each scenario
        for scenario_config in config.get("scenarios", []):
            # Merge global + scenario specific config
            merged_config = {**global_config, **scenario_config}

            scenario = TestScenario(
                symbol=merged_config["symbol"],
                start_date=merged_config["start_date"],
                end_date=merged_config["end_date"],
                max_ticks=merged_config.get("max_ticks", 1000),
                data_mode=merged_config.get("data_mode", "realistic"),
                strategy_config=merged_config.get("strategy_config", {}),
                name=merged_config.get(
                    "name", f"{merged_config['symbol']}_test")
            )
            scenarios.append(scenario)

        logger.info(f"Loaded {len(scenarios)} scenarios")
        return scenarios

    def save_config(self, scenarios: List[TestScenario], config_file: str):
        """
        Save scenarios to JSON config file

        Args:
            scenarios: List of TestScenario objects
            config_file: Output filename
        """
        config_path = self.config_path / config_file

        config = {
            "version": "1.0",
            "created": datetime.now().isoformat(),
            "global": {
                "data_mode": "realistic",
                "strategy_config": {
                    "rsi_period": 14,
                    "envelope_period": 20,
                    "envelope_deviation": 0.02,
                    "execution": {
                        "parallel_workers": True,
                        "artificial_load_ms": 5.0,
                        "max_parallel_scenarios": 4
                    }
                }
            },
            "scenarios": []
        }

        for scenario in scenarios:
            scenario_dict = {
                "name": scenario.name,
                "symbol": scenario.symbol,
                "start_date": scenario.start_date,
                "end_date": scenario.end_date,
                "max_ticks": scenario.max_ticks,
                "strategy_config": scenario.strategy_config
            }
            config["scenarios"].append(scenario_dict)

        with open(config_path, 'w') as f:
            json.dump(config, f, indent=2)

        print(f"Saved {len(scenarios)} scenarios to: {config_path}")
