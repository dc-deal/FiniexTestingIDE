from multiprocessing import Queue
from typing import Optional

from python.framework.types.live_stats_config_types import LiveStatsExportConfig, ScenarioStatus
from python.framework.types.process_data_types import ProcessScenarioConfig


def send_status_update_process(
    live_queue: Optional[Queue],
    config: ProcessScenarioConfig,
    status: ScenarioStatus
) -> None:
    """
    Send status update to live queue in Process
    """
    # convert for global broadcasting function
    broadcast_status_update(live_queue=live_queue,
                            scenario_index=config.scenario_index,
                            scenario_name=config.name,
                            live_stats_config=config.live_stats_config,
                            status=status)


def broadcast_status_update(
    live_queue: Optional[Queue],
    scenario_index: int,
    scenario_name: str,
    live_stats_config: LiveStatsExportConfig,
    status: ScenarioStatus
) -> None:
    """
    Send status update to live queue, from anywhere

    Args:
        live_queue: Queue for live updates
        scenario_index: int,
        scenario_name: str,
        live_stats_config_enabled : bool,
        status: Status string (ScenarioStatus value)
    """
    if not live_queue or not live_stats_config.enabled:
        return

    try:
        # === NORMAL STATUS UPDATE (alle Szenarien) ===
        live_queue.put_nowait({
            "type": "status",
            "scenario_index": scenario_index,
            "scenario_name": scenario_name,
            "status": status
        })
    except:
        pass  # Queue full - skip update
