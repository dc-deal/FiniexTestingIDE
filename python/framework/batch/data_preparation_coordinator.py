"""
FiniexTestingIDE - Data Preparation Coordinator
Phase 1: Coordinates tick, bar, and broker data preparation

Extracted from BatchOrchestrator to separate data preparation logic.
"""
from python.configuration.app_config_manager import AppConfigManager
from python.data_management.index.tick_index_manager import TickIndexManager
from python.framework.data_preparation.shared_data_preparator import SharedDataPreparator
from python.framework.data_preparation.broker_data_preparator import BrokerDataPreparator
from python.framework.types.process_data_types import ProcessDataPackage, RequirementsMap
from python.framework.types.scenario_set_types import SingleScenario
from python.framework.types.live_stats_config_types import ScenarioStatus
from python.framework.logging.abstract_logger import AbstractLogger
from typing import Dict, List, Optional, Protocol


class StatusBroadcaster(Protocol):
    """Protocol for status broadcasting (e.g., LiveStatsCoordinator)"""

    def broadcast_status(self, status: ScenarioStatus) -> None: ...


class DataPreparationCoordinator:
    """
    Coordinates all data preparation for batch execution.

    Responsibilities:
    - Load tick data with status broadcasting
    - Load bar data with status broadcasting
    - Prepare broker configurations
    - Package all data into ProcessDataPackage
    """

    def __init__(
        self,
        scenarios: List[SingleScenario],
        logger: AbstractLogger,
        app_config: AppConfigManager
    ):
        """
        Initialize data preparation coordinator.

        Args:
            scenarios: List of scenarios for broker config preparation
            logger: Logger instance for status messages
        """
        self._scenarios = scenarios
        self._logger = logger
        self._data_preparator = SharedDataPreparator(logger)
        self._broker_preparator = BrokerDataPreparator(scenarios, logger)
        self._app_config = app_config

    def get_tick_index_manager(self) -> TickIndexManager:
        return self._data_preparator.tick_index_manager

    def prepare(
        self,
        requirements_map: RequirementsMap,
        status_broadcaster: Optional[StatusBroadcaster] = None
    ) -> Dict[int, ProcessDataPackage]:  # Per-scenario packages
        """
        Prepare scenario-specific data packages.

        OPTIMIZATION: Creates individual packages per scenario instead of
        one global package. Reduces pickle overhead by 5x.

        Args:
            requirements_map: Aggregated requirements from all scenarios
            status_broadcaster: Optional status broadcaster for live updates

        Returns:
            Dict mapping scenario_index → ProcessDataPackage
        """
        self._logger.info("📄 Phase 1: Preparing shared data...")

        # ========================================================================
        # 1.1 PREPARE BROKER CONFIG FIRST
        # ========================================================================
        if status_broadcaster:
            status_broadcaster.broadcast_status(ScenarioStatus.WARMUP_TRADER)

        broker_configs = self._broker_preparator.prepare()

        # ========================================================================
        # 1.2 PREPARE SCENARIO-SPECIFIC PACKAGES
        # ========================================================================
        # Broadcast status: Loading ticks
        if status_broadcaster:
            status_broadcaster.broadcast_status(
                ScenarioStatus.WARMUP_DATA_TICKS)

        # Use prepare_scenario_packages instead of separate prepare_ticks/bars
        scenario_packages = self._data_preparator.prepare_scenario_packages(
            requirements_map=requirements_map,
            scenarios=self._scenarios,
            broker_configs=broker_configs
        )

        # Log summary
        total_packages = len(scenario_packages)
        self._logger.info(
            f"✅ Data prepared: {total_packages} scenario-specific packages"
        )

        return scenario_packages

    def get_broker_scenario_map(self) -> dict:
        """
        Get broker-to-scenario mapping.

        Returns:
            Broker scenario map from broker preparator
        """
        return self._broker_preparator.get_broker_scenario_map()
