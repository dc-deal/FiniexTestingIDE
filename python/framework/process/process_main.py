

from multiprocessing import Queue
import time
import traceback
from typing import Optional
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.process.process_tick_loop import execute_tick_loop
from python.framework.process.process_live_queue_helper import send_status_update_process
from python.framework.process.process_startup_preparation import process_startup_preparation
from python.framework.types.live_stats_config_types import ScenarioStatus
from python.framework.types.process_data_types import ProcessDataPackage, ProcessResult, ProcessScenarioConfig
from python.framework.utils.file_utils import file_name_for_scenario, pad_int


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
        send_status_update_process(
            live_queue, config, ScenarioStatus.INIT_PROCESS)

        # === CREATE SCENARIO LOGGER ===
        # Use shared run_timestamp from BatchOrchestrator
        scenario_logger = ScenarioLogger(
            scenario_set_name=config.scenario_set_name,
            scenario_name=file_name_for_scenario(
                config.scenario_index, config.name),
            run_timestamp=config.run_timestamp
        )
        scenario_logger.info(f"⏱️  Process started at {start_time}")

        # === STARTUP PREPARATION ===
        prepared_objects = process_startup_preparation(
            config, shared_data, scenario_logger)
        scenario_logger = prepared_objects.scenario_logger
        scenario_logger.debug(
            f"🔄 Process preparation finished")

        # === STATUS: RUNNING ===
        send_status_update_process(live_queue, config, ScenarioStatus.RUNNING)

        # === TICK LOOP EXECUTION ===
        # Extract barrier from shared_data
        tick_loop_results = execute_tick_loop(
            config, prepared_objects, live_queue)
        scenario_logger.debug(
            f"🔄 Execute tick loop finished")

        # === Process Final status ===
        success = True
        error_type = None
        error_message = None
        error_traceback = None

        # === Get Log Buffer ===
        log_buffer = scenario_logger.get_buffer()
        errors_in_buffer = scenario_logger.get_buffer_errors()
        scenario_logger.close()

        # === Tick loop Error Check ===
        tick_loop_error = tick_loop_results.tick_loop_error
        if (tick_loop_error is not None):
            # tick loop failed, pare error, sucess is false.
            success = False
            error_type = type(tick_loop_error).__name__
            error_message = str(tick_loop_error)
            error_traceback = ''.join(traceback.format_exception(
                type(tick_loop_error),
                tick_loop_error,
                tick_loop_error.__traceback__
            ))
            send_status_update_process(live_queue, config,
                                       ScenarioStatus.FINISHED_WITH_ERROR)

        # === Errors in Buffer check ===
        if len(errors_in_buffer) > 0 and tick_loop_error is None:
            # Logged errors WITHOUT exception
            success = False
            error_type = "LoggedErrors"
            error_message = f"Scenario logged {len(errors_in_buffer)} ERROR(s)"
            send_status_update_process(
                live_queue, config, ScenarioStatus.FINISHED_WITH_ERROR)

        if success == True:
            send_status_update_process(
                live_queue, config, ScenarioStatus.COMPLETED)

        # === BUILD RESULT ===
        result = ProcessResult(
            success=success,
            scenario_name=config.name,
            scenario_index=config.scenario_index,
            execution_time_ms=time.time() - start_time,
            tick_loop_results=tick_loop_results,
            scenario_logger_buffer=log_buffer,
            error_type=error_type,
            error_message=error_message,
            traceback=error_traceback
        )
        scenario_logger.debug(
            f"🕐 {config.name} returning at {time.time()}")
        return result

    except Exception as e:
        # HARD Error handling: Return error details for logging
        log_buffer = None
        try:
            # try to fetch Log, if possible.
            log_buffer = scenario_logger.get_buffer()
            send_status_update_process(live_queue, config,
                                       ScenarioStatus.FINISHED_WITH_ERROR)
        except:
            print(e)
            pass
        return ProcessResult(
            success=False,
            scenario_name=config.name,
            scenario_index=config.scenario_index,
            error_type=type(e).__name__,
            error_message=str(e),
            traceback=traceback.format_exc(),
            scenario_logger_buffer=log_buffer
        )
