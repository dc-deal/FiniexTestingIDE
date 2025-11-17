

from multiprocessing import Queue
import time
import traceback
from typing import Optional
from python.components.logger.scenario_logger import ScenarioLogger
from python.framework.process.process_tick_loop import execute_tick_loop
from python.framework.process.process_live_queue_helper import send_status_update
from python.framework.process.process_startup_prepreation import process_startup_preparation
from python.framework.types.live_stats_config_types import ScenarioStatus
from python.framework.types.process_data_types import ProcessDataPackage, ProcessResult, ProcessScenarioConfig


def process_main(
    config: ProcessScenarioConfig,
    shared_data: ProcessDataPackage,
    live_queue: Optional[Queue] = None
) -> ProcessResult:
    """
    Main process entry point.

    TOP-LEVEL FUNCTION: Can be called by ProcessPoolExecutor.
    Creates all objects, runs tick loop, returns results.

    Args:
        config: Serializable scenario configuration
        shared_data: Shared read-only data (CoW)

    Returns:
        ProcessResult with execution results or error details
    """
    try:
        start_time = time.time()
        # === STATUS: INIT_PROCESS ===
        send_status_update(live_queue, config, ScenarioStatus.INIT_PROCESS)

        # === CREATE SCENARIO LOGGER ===
        # Use shared run_timestamp from BatchOrchestrator
        scenario_logger = ScenarioLogger(
            scenario_set_name=config.scenario_set_name,
            scenario_name=config.name,
            run_timestamp=config.run_timestamp
        )

        # === STARTUP PREPARATION ===
        prepared_objects = process_startup_preparation(
            config, shared_data, scenario_logger)
        scenario_logger = prepared_objects.scenario_logger
        scenario_logger.debug(
            f"🔄 Process preperation finished")

        # === STATUS: RUNNING ===
        send_status_update(live_queue, config, ScenarioStatus.RUNNING)

        # === TICK LOOP EXECUTION ===
        tick_loop_results = execute_tick_loop(
            config, prepared_objects, live_queue)
        scenario_logger.debug(
            f"🔄 Execute tick loop finished")

        # === BUILD RESULT ===
        # logger.run_timestamp - start

        log_buffer = scenario_logger.get_buffer()
        scenario_logger.close()

        # === STATUS: COMPLETED ===
        send_status_update(live_queue, config, ScenarioStatus.COMPLETED)

        result = ProcessResult(
            success=True,
            scenario_name=config.name,
            symbol=config.symbol,
            scenario_index=config.scenario_index,
            execution_time_ms=time.time() - start_time,
            tick_loop_results=tick_loop_results,
            scenario_logger_buffer=log_buffer
        )
        scenario_logger.debug(
            f"🕐 {config.name} returning at {time.time()}")
        return result

    except Exception as e:
        # Error handling: Return error details for logging
        log_buffer = None
        try:
            # try to fetch Log, if possible.
            log_buffer = scenario_logger.get_buffer()
            send_status_update(live_queue, config,
                               ScenarioStatus.FINISHED_WITH_ERROR)
        except:
            print(e)
            pass
        return ProcessResult(
            success=False,
            scenario_name=config.name,
            symbol=config.symbol,
            scenario_index=config.scenario_index,
            error_type=type(e).__name__,
            error_message=str(e),
            traceback=traceback.format_exc(),
            scenario_logger_buffer=log_buffer
        )
