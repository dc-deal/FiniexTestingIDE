"""
FiniexTestingIDE - Live Stats Coordinator
Manages live statistics cache and status broadcasting

Extracted from BatchOrchestrator to separate live stats management.
"""
import queue
from multiprocessing import Queue
from typing import Dict, List, Optional

from python.framework.logging.bootstrap_logger import get_global_logger
from python.framework.types.live_types.live_core_snapshot_types import LiveCoreSnapshot
from python.framework.types.live_types.live_scenario_stats_types import (
    LiveScenarioStats,
    LiveStatusFrame,
)
from python.framework.types.live_types.live_stats_config_types import ScenarioStatus
from python.framework.types.scenario_types.scenario_set_types import SingleScenario

vLog = get_global_logger()


class LiveStatsCoordinator:
    """
    Manages live statistics cache and status broadcasting.

    Responsibilities:
    - Initialize live stats cache for all scenarios
    - Broadcast status updates to live queue
    - Maintain scenario status state
    """

    def __init__(
        self,
        scenarios: List[SingleScenario],
        live_queue: Optional[Queue],
        enabled: bool
    ):
        """
        Initialize live stats coordinator.

        Args:
            scenarios: List of scenarios to track
            live_queue: Multiprocessing queue for live updates (None if disabled)
            enabled: Whether live stats are enabled
        """
        self._scenarios = scenarios
        self._live_queue = live_queue
        self._enabled = enabled
        self._live_stats_cache: Dict[int, LiveScenarioStats] = {}

        if enabled:
            self._init_live_stats()

    def _init_live_stats(self) -> None:
        """Initialize LiveScenarioStats cache for all scenarios."""
        for idx, scenario in enumerate(self._scenarios):
            self._live_stats_cache[idx] = LiveScenarioStats(
                core=LiveCoreSnapshot(symbol=scenario.symbol),
                scenario_name=scenario.name,
                scenario_index=idx,
                status=ScenarioStatus.INITIALIZED
            )

    def broadcast_status(self, status: ScenarioStatus) -> None:
        """
        Broadcast status update for all scenarios.

        Args:
            status: New status for all scenarios
        """
        if not self._enabled:
            return

        failure: Optional[Exception] = None
        for idx, stats in self._live_stats_cache.items():
            stats.status = status

            # Built outside the try: a frame that cannot be built is a defect and propagates
            frame = LiveStatusFrame(
                scenario_index=idx,
                scenario_name=stats.scenario_name,
                status=status,
            )
            try:
                self._live_queue.put_nowait(frame)
            except queue.Full:
                pass  # Queue full - skip update
            except Exception as error:
                failure = failure or error
        if failure is not None:
            # The progress screen is the only reader: reported once per broadcast, never
            # raised (#586)
            vLog.error(f'❌ [DISPLAY] A status broadcast was not sent — '
                       f'{type(failure).__name__}: {failure}. The progress screen misses it; '
                       f'the batch is unaffected.')
