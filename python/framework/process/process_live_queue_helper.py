import queue
from multiprocessing import Queue
from typing import Optional

from python.framework.logging.bootstrap_logger import get_global_logger
from python.framework.types.live_types.live_scenario_stats_types import LiveStatusFrame
from python.framework.types.live_types.live_stats_config_types import (
    LiveStatsExportConfig,
    ScenarioStatus,
)
from python.framework.types.process_data_types import ProcessScenarioConfig

vLog = get_global_logger()


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

    # === NORMAL STATUS UPDATE (all scenarios) ===
    # Built outside the try: a frame that cannot be built is a defect and propagates (#586)
    frame = LiveStatusFrame(
        scenario_index=scenario_index,
        scenario_name=scenario_name,
        status=status,
    )
    try:
        live_queue.put_nowait(frame)
    except queue.Full:
        pass  # Queue full - skip update
    except Exception as error:
        # The progress screen is the only reader: reported, never raised (#586)
        vLog.error(f'❌ [DISPLAY] A scenario status frame was not sent — '
                   f'{type(error).__name__}: {error}. The progress screen misses it; '
                   f'the scenario is unaffected.')
