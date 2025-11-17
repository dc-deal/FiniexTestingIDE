from multiprocessing import Queue
from typing import Optional

from python.framework.types.live_stats_config_types import ScenarioStatus
from python.framework.types.process_data_types import ProcessScenarioConfig


def send_status_update(
    live_queue: Optional[Queue],
    config: ProcessScenarioConfig,
    status: ScenarioStatus
) -> None:
    """
    Send status update to live queue.

    Args:
        live_queue: Queue for live updates
        config: Scenario configuration
        status: Status string (ScenarioStatus value)
    """
    if not live_queue or not config.live_stats_config.enabled:
        return

    try:
        # === NORMAL STATUS UPDATE (alle Szenarien) ===
        live_queue.put_nowait({
            "type": "status",
            "scenario_index": config.scenario_index,
            "scenario_name": config.name,
            "status": status
        })
    except:
        pass  # Queue full - skip update
