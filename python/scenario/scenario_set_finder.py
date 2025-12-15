"""
FiniexTestingIDE - Scenario Set Finder
Discovers and extracts metadata from scenario set config files

FIX01: Added time analysis and strategy info extraction
"""

from pathlib import Path
from typing import List
from datetime import datetime
import json

from python.framework.types.scenario_set_types import ScenarioSetMetadata
from python.scenario.scenario_config_loader import ScenarioConfigLoader

from python.framework.logging.bootstrap_logger import get_global_logger
vLog = get_global_logger()


class ScenarioSetFinder:
    """
    Manages discovery and metadata extraction for scenario set configs

    Provides both fast (file listing) and slow (full validation) operations
    """

    def __init__(self, config_path: str = "./configs/scenario_sets/"):
        """
        Initialize finder

        Args:
            config_path: Directory containing scenario set config files
        """
        self._config_path = Path(config_path)
        self._config_loader = ScenarioConfigLoader(str(self._config_path))

    def list_available_files(self) -> List[Path]:
        """
        Fast: List all .json files in config directory

        Returns:
            Sorted list of .json file paths
        """
        if not self._config_path.exists():
            vLog.warning(f"Config path does not exist: {self._config_path}")
            return []

        return sorted(self._config_path.glob("*.json"))

    def get_scenario_set_details(self, filename: str) -> ScenarioSetMetadata:
        """
        Slow: Load and validate scenario set, extract full metadata

        Uses ScenarioConfigLoader for full validation and parsing

        Args:
            filename: Config filename (e.g., "eurusd_3_windows.json")

        Returns:
            ScenarioSetMetadata with full details including time analysis

        Raises:
            FileNotFoundError: If config file doesn't exist
            Exception: If config is invalid or can't be loaded
        """
        config_path = self._config_path / filename

        if not config_path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")

        # Load via ScenarioConfigLoader (full validation)
        loaded_scenario_set = self._config_loader.load_config(filename)
        scenarios = loaded_scenario_set.get_all_scenarios()

        # Load raw JSON for full counts
        with open(config_path, 'r') as f:
            raw_data = json.load(f)

        # === BASIC COUNTS ===
        total_count = len(raw_data.get('scenarios', []))
        enabled_count = len(scenarios)
        disabled_count = total_count - enabled_count

        # Extract symbols (unique)
        symbols = list(set(scenario.symbol for scenario in scenarios))

        # === TIME ANALYSIS ===
        timespan_scenarios = []
        tick_scenarios = []

        for scenario in scenarios:
            if scenario.max_ticks is None:
                # Timespan mode - calculate duration
                start_dt = datetime.fromisoformat(scenario.start_date)
                end_dt = datetime.fromisoformat(scenario.end_date)
                duration_seconds = (end_dt - start_dt).total_seconds()
                timespan_scenarios.append(duration_seconds)
            else:
                # Tick mode
                tick_scenarios.append(scenario.max_ticks)

        timespan_scenario_count = len(timespan_scenarios)
        total_timespan_seconds = sum(timespan_scenarios)
        tick_scenario_count = len(tick_scenarios)
        total_ticks = sum(tick_scenarios)

        # === DECISION LOGIC ANALYSIS ===
        decision_logic_types = set()
        for scenario in scenarios:
            logic_type = scenario.strategy_config.get('decision_logic_type')
            if logic_type:
                decision_logic_types.add(logic_type)

        if len(decision_logic_types) == 1:
            decision_logic_type = list(decision_logic_types)[0]
            is_mixed_decision_logic = False
        elif len(decision_logic_types) > 1:
            decision_logic_type = None
            is_mixed_decision_logic = True
        else:
            decision_logic_type = None
            is_mixed_decision_logic = False

        # === WORKER COUNT ANALYSIS ===
        worker_counts = set()
        for scenario in scenarios:
            workers = scenario.strategy_config.get('workers', {})
            worker_counts.add(len(workers))

        if len(worker_counts) == 1:
            worker_count = list(worker_counts)[0] if worker_counts else None
            is_mixed_workers = False
        elif len(worker_counts) > 1:
            worker_count = None
            is_mixed_workers = True
        else:
            worker_count = None
            is_mixed_workers = False

        return ScenarioSetMetadata(
            filename=filename,
            scenario_set_name=loaded_config.scenario_set_name,
            total_count=total_count,
            enabled_count=enabled_count,
            disabled_count=disabled_count,
            symbols=symbols,
            config_path=config_path,
            timespan_scenario_count=timespan_scenario_count,
            total_timespan_seconds=total_timespan_seconds,
            tick_scenario_count=tick_scenario_count,
            total_ticks=total_ticks,
            decision_logic_type=decision_logic_type,
            is_mixed_decision_logic=is_mixed_decision_logic,
            worker_count=worker_count,
            is_mixed_workers=is_mixed_workers
        )

    def list_all_with_details(self) -> List[ScenarioSetMetadata]:
        """
        Slow: Load all scenario sets and extract full metadata

        Attempts to load each file, skips invalid ones with warning

        Returns:
            List of ScenarioSetMetadata for all valid config files
        """
        results = []

        for file_path in self.list_available_files():
            try:
                metadata = self.get_scenario_set_details(file_path.name)
                results.append(metadata)
            except Exception as e:
                vLog.warning(
                    f"⚠️ Skipping invalid config: {file_path.name}"
                )

        return results
