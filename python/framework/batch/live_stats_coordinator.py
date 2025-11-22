"""
FiniexTestingIDE - Live Stats Coordinator
Manages live statistics cache and status broadcasting

Extracted from BatchOrchestrator to separate live stats management.
"""
from python.framework.types.live_scenario_stats_types import LiveScenarioStats
from python.framework.types.live_stats_config_types import ScenarioStatus
from python.framework.types.scenario_set_types import SingleScenario
from typing import Dict, List, Optional
from multiprocessing import Queue


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
                scenario_name=scenario.name,
                symbol=scenario.symbol,
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

        for idx, stats in self._live_stats_cache.items():
            stats.status = status

            try:
                self._live_queue.put_nowait({
                    "type": "status",
                    "scenario_index": idx,
                    "scenario_name": stats.scenario_name,
                    "status": status.value
                })
            except:
                pass  # Queue full - skip update
