"""
FiniexTestingIDE - Broker Data Preparator
Prepares broker configurations for batch execution

Responsibilities:
- Load unique broker configs from scenario definitions
- Cache config_path to avoid duplicate JSON loading
- Assign broker_type to scenarios
- Serialize configs for subprocess sharing (CoW-safe)
- Log concise broker overview
"""

from typing import Dict, List, Any
from dataclasses import dataclass

from python.configuration.market_config_manager import MarketConfigManager
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.factory.broker_config_factory import BrokerConfigFactory
from python.framework.trading_env.broker_config import BrokerConfig, BrokerType
from python.framework.batch_reporting.broker_info_renderer import BrokerInfoRenderer
from python.framework.types.scenario_set_types import BrokerScenarioInfo, SingleScenario
from python.framework.types.validation_types import ValidationResult


class BrokerDataPreparator:
    """
    Prepares broker configurations for batch execution.

    Performance:
    - Caches broker configs by config_path to avoid duplicate JSON loading
    - Example: 5 scenarios with same path = only 1x file I/O

    Usage:
        preparator = BrokerDataPreparator(scenarios, logger)
        broker_configs = preparator.prepare()
        shared_data.broker_configs = broker_configs
    """

    def __init__(self, scenarios: List[SingleScenario], logger: ScenarioLogger):
        """
        Initialize preparator.

        Args:
            scenarios: List of scenarios to process
            logger: Logger for broker overview output
        """
        self.scenarios = scenarios
        self.logger = logger
        self._config_path_cache: Dict[str, BrokerConfig] = {}
        self._broker_scenario_map: Dict[BrokerType, BrokerScenarioInfo] = {}

    def get_broker_scenario_map(self) -> Dict[BrokerType, BrokerScenarioInfo]:
        return self._broker_scenario_map

    def prepare(self) -> Dict[BrokerType, Dict[str, Any]]:
        """
        Main entry point - loads, maps, and logs broker configurations.

        Side effects:
        - Assigns broker_type to each scenario
        - Logs broker overview to logger

        Returns:
            Serialized broker configs ready for subprocess sharing
        """
        self._load_and_map_broker_configs()
        self._log_broker_overview()
        return self._serialize_broker_configs()

    def _load_and_map_broker_configs(self) -> None:
        """
        Load unique broker configs and build scenario mapping.

        Performance optimization:
        - Uses config_path cache to avoid duplicate loading
        - Each unique path is loaded only once via BrokerConfigFactory

        Side effects:
        - Populates self._config_path_cache
        - Populates self._broker_scenario_map
        - Assigns scenario.broker_type for each scenario
        """
        # Initialize MarketConfigManager once for all scenarios
        market_config = MarketConfigManager()

        for scenario in self.scenarios:
            # Get config_path from MarketConfigManager via data_broker_type
            # This replaces: scenario.trade_simulator_config.get("broker_config_path")
            config_path = market_config.get_broker_config_path(
                scenario.data_broker_type
            )

            # Load broker config (cached: only once per unique config_path)
            if config_path not in self._config_path_cache:
                self._config_path_cache[config_path] = (
                    BrokerConfigFactory.build_broker_config(config_path)
                )

            broker_config = self._config_path_cache[config_path]
            broker_type = broker_config.broker_type

            # Assign broker_type to scenario (needed for subprocess deserialization)
            scenario.broker_type = broker_type

            # Initialize broker entry if new
            if broker_type not in self._broker_scenario_map:
                self._broker_scenario_map[broker_type] = BrokerScenarioInfo(
                    config_path=config_path,
                    scenarios=[],
                    symbols=set(),
                    broker_config=broker_config
                )

            # Track scenario assignment
            self._broker_scenario_map[broker_type].scenarios.append(
                scenario.name)
            # Track symbols, but do not resolve (performance)
            # Will be resolved later in the reports.
            # Can be resolved with broker_config.get_symbol_specification
            self._broker_scenario_map[broker_type].symbols.add(scenario.symbol)

    def _serialize_broker_configs(self) -> Dict[BrokerType, Dict[str, Any]]:
        """
        Serialize broker configs for subprocess sharing with symbol filtering.

        OPTIMIZATION: Only includes symbols actually used by scenarios.
        Reduces pickle size from ~1.5 MB (956 symbols) to ~2 KB (1-10 symbols).

        Returns raw JSON dicts - CoW-safe and pickleable.
        Subprocesses can re-hydrate adapters from these dicts.

        Returns:
            Dict mapping broker_type to filtered serialized config dict
        """
        # Collect all symbols needed across all scenarios
        all_needed_symbols = {scenario.symbol for scenario in self.scenarios}

        serialized_configs: Dict[BrokerType, Dict[str, Any]] = {}

        for broker_type, info in self._broker_scenario_map.items():
            # Get full serialized config
            full_dict = BrokerConfigFactory.to_serializable_dict(
                info.broker_config)

            # Filter: Only keep symbols that are actually used
            filtered_dict = {
                'broker_info': full_dict['broker_info'],
                'trading_permissions': full_dict['trading_permissions'],
                'fee_structure': full_dict['fee_structure'],
                'symbols': {
                    symbol: spec
                    for symbol, spec in full_dict['symbols'].items()
                    if symbol in all_needed_symbols
                }
            }

            serialized_configs[broker_type] = filtered_dict

        return serialized_configs

    def _log_broker_overview(self) -> None:
        """
        Log concise broker overview with scenario assignments.

        Output format:
        - Header with unique broker count
        - Per broker: single-line info + config path + scenario list
        """
        num_brokers = len(self._broker_scenario_map)
        self.logger.info(
            f"🏦 Broker Configuration: {num_brokers} unique broker(s) loaded"
        )

        for broker_type, info in self._broker_scenario_map.items():
            broker_spec = info.broker_config.get_broker_specification()
            broker_info_line = BrokerInfoRenderer.render_single_line(
                broker_spec=broker_spec
            )

            num_scenarios = len(info.scenarios)
            broker_scenario_names = ', '.join(info.scenarios)

            self.logger.info(f"   {broker_info_line}")
            self.logger.debug(f"      Config: {info.config_path}")
            self.logger.debug(
                f"      Used by {num_scenarios} scenario(s): {broker_scenario_names}"
            )
