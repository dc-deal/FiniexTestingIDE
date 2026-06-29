

from multiprocessing import Queue
import time
import traceback
from typing import Optional
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.process.process_tick_loop import execute_tick_loop
from python.framework.trading_env.decision_event_dispatcher import DecisionEventDispatcher
from python.framework.process.process_live_queue_helper import send_status_update_process
from python.framework.process.process_startup_preparation import process_startup_preparation
from python.framework.reporting.diagnostics_csv_sink import flush_decision_diagnostics
from python.framework.validators.component_metadata_advisory import surface_decision_logic_metadata
from python.framework.types.live_types.live_stats_config_types import ScenarioStatus
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
            run_timestamp=config.run_timestamp,
            run_group=config.run_group,
            use_scenario_logs_subdir=True
        )
        scenario_logger.info(f"⏱️  Process started at {start_time}")

        # === STARTUP PREPARATION ===
        (worker_coordinator,
         trade_simulator,
         bar_rendering_controller,
         decision_logic,
         scenario_logger,
         ticks) = process_startup_preparation(
            config, shared_data, scenario_logger)
        scenario_logger.debug(
            f"🔄 Process preparation finished")

        # Component metadata advisory (#118 Stage 0) — version line + soft market-fit warning
        surface_decision_logic_metadata(
            decision_logic, config.broker_type, config.symbol, scenario_logger)

        # === DECISION EVENT CHANNEL (#348) ===
        # Built only when the active decision logic subscribes to events.
        decision_event_dispatcher = DecisionEventDispatcher.create_if_subscribed(
            decision_logic=decision_logic,
            executor=trade_simulator,
            logger=scenario_logger,
        )

        # === STATUS: RUNNING ===
        send_status_update_process(live_queue, config, ScenarioStatus.RUNNING)

        # === TICK LOOP EXECUTION ===
        tick_loop_results = execute_tick_loop(
            config, worker_coordinator, trade_simulator,
            bar_rendering_controller, decision_logic,
            scenario_logger, ticks, live_queue,
            decision_event_dispatcher)
        scenario_logger.debug(
            f"🔄 Execute tick loop finished")

        # === DIAGNOSTICS CSV (#376) ===
        # Flush algo-declared diagnostics sinks next to events_<scenario>.csv.
        # Suffix matches process_result.scenario_name (config.name) for alignment.
        flush_decision_diagnostics(
            decision_logic, scenario_logger.get_log_dir(),
            scenario_suffix=config.name)

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
