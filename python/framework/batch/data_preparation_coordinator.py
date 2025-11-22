"""
FiniexTestingIDE - Data Preparation Coordinator
Phase 1: Coordinates tick, bar, and broker data preparation

Extracted from BatchOrchestrator to separate data preparation logic.
"""
from python.framework.data_preperation.shared_data_preparator import SharedDataPreparator
from python.framework.data_preperation.broker_data_preperator import BrokerDataPreparator
from python.framework.types.process_data_types import ProcessDataPackage, RequirementsMap
from python.framework.types.scenario_set_types import SingleScenario
from python.framework.types.live_stats_config_types import ScenarioStatus
from python.components.logger.abstract_logger import AbstractLogger
from typing import List, Optional, Protocol


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
        logger: AbstractLogger
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

    def prepare(
        self,
        requirements_map: RequirementsMap,
        status_broadcaster: Optional[StatusBroadcaster] = None
    ) -> ProcessDataPackage:
        """
        Prepare all shared data (ticks, bars, broker configs).

        Args:
            requirements_map: Aggregated requirements from all scenarios
            status_broadcaster: Optional status broadcaster for live updates

        Returns:
            ProcessDataPackage with all prepared data
        """
        self._logger.info("🔄 Phase 1: Preparing shared data...")

        # ========================================================================
        # 1.1 PREPARE BARS & TICKS
        # ========================================================================

        # Broadcast status: Loading ticks
        if status_broadcaster:
            status_broadcaster.broadcast_status(
                ScenarioStatus.WARMUP_DATA_TICKS)

        # Phase 1A: Load Ticks
        ticks_data, tick_counts, tick_ranges = self._data_preparator.prepare_ticks(
            requirements_map.tick_requirements
        )

        # Broadcast status: Loading bars
        if status_broadcaster:
            status_broadcaster.broadcast_status(
                ScenarioStatus.WARMUP_DATA_BARS)

        # Phase 1B: Load Bars
        bars_data, bar_counts = self._data_preparator.prepare_bars(
            requirements_map.bar_requirements
        )

        # Phase 1C: Package Data
        shared_data = ProcessDataPackage(
            ticks=ticks_data,
            bars=bars_data,
            tick_counts=tick_counts,
            tick_ranges=tick_ranges,
            bar_counts=bar_counts,
            broker_configs=None
        )

        # Log summary
        total_ticks = sum(tick_counts.values())
        total_bars = sum(bar_counts.values())

        self._logger.info(
            f"✅ Data prepared: {total_ticks:,} ticks, {total_bars:,} bars "
            f"({len(ticks_data)} tick sets, {len(bars_data)} bar sets)"
        )

        # Broadcast status: Warming up trader
        if status_broadcaster:
            status_broadcaster.broadcast_status(ScenarioStatus.WARMUP_TRADER)

        # ========================================================================
        # 1.2 PREPARE BROKER CONFIG
        # ========================================================================
        shared_data.broker_configs = self._broker_preparator.prepare()

        return shared_data

    def get_broker_scenario_map(self) -> dict:
        """
        Get broker-to-scenario mapping.

        Returns:
            Broker scenario map from broker preparator
        """
        return self._broker_preparator.get_broker_scenario_map()
