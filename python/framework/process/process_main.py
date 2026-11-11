

import time
import traceback
from multiprocessing import Queue
from typing import Optional

from python.framework.constants.run_layout_constants import RUN_TYPE_SIMULATION
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.process.process_live_queue_helper import send_status_update_process
from python.framework.process.process_pipeline_bundle import ProcessPipelineBundle
from python.framework.process.process_startup_preparation import process_startup_preparation
from python.framework.process.process_tick_loop import execute_tick_loop
from python.framework.process.state_frame_recorder import StateFrameRecorder
from python.framework.reporting.diagnostics_csv_sink import flush_decision_diagnostics
from python.framework.store.entry_members.run_entry_writer import RunEntryWriter
from python.framework.trading_env.decision_event_dispatcher import DecisionEventDispatcher
from python.framework.types.live_types.live_stats_config_types import ScenarioStatus
from python.framework.types.log_level import LogLevel
from python.framework.types.process_data_types import (
    LOGGED_ERRORS_TYPE,
    ProcessDataPackage,
    ProcessResult,
    ProcessScenarioConfig,
)
from python.framework.types.store_member_types import RunEntryMemberId
from python.framework.utils.file_utils import file_name_for_scenario, scenario_log_names
from python.framework.utils.timeframe_config_utils import TimeframeConfig


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
        # The duration runs on the MONOTONIC clock: two wall-clock readings can come out negative
        # when NTP steps the clock. `start_time` above stays the wall-clock point the log names.
        start_monotonic = time.perf_counter()

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
            run_id=config.run_id,
            log_root_override=config.log_root,
            log_member=RunEntryMemberId.SCENARIO_LOG,
            log_names=scenario_log_names(config.scenario_index, config.name),
            run_type=RUN_TYPE_SIMULATION,
            # This log IS the run's tick-by-tick record, so every line carries the run's own
            # time. The clock itself is attached once the executor exists (startup preparation).
            event_time_column=True
        )
        scenario_logger.info(f'⏱️  Process started at {start_time}')

        # === STARTUP PREPARATION ===
        pipeline = process_startup_preparation(
            config, shared_data, scenario_logger)
        worker_coordinator = pipeline.worker_coordinator
        trade_simulator = pipeline.trade_simulator
        bar_rendering_controller = pipeline.bar_rendering_controller
        decision_logic = pipeline.decision_logic
        ticks = pipeline.ticks
        scenario_logger.debug(
            '🔄 Process preparation finished')

        # === DECISION EVENT CHANNEL (#348) ===
        # Built only when the active decision logic subscribes to events.
        decision_event_dispatcher = DecisionEventDispatcher.create_if_subscribed(
            decision_logic=decision_logic,
            executor=trade_simulator,
            logger=scenario_logger,
        )

        # === STATE FRAMES (#579 part B item 3) ===
        # Only when the scenario asks: a backtest of a session's window records the session's frame
        # on the session's slots, so the divergence report can lay the two side by side (#512).
        state_frames = _open_state_frames(config, pipeline, scenario_logger)

        # === STATUS: RUNNING ===
        send_status_update_process(live_queue, config, ScenarioStatus.RUNNING)

        # === TICK LOOP EXECUTION ===
        tick_loop_results = execute_tick_loop(
            config, worker_coordinator, trade_simulator,
            bar_rendering_controller, decision_logic,
            scenario_logger, ticks, live_queue,
            decision_event_dispatcher, state_frames)
        scenario_logger.debug(
            '🔄 Execute tick loop finished')

        # === DIAGNOSTICS CSV (#376) ===
        # Flush algo-declared diagnostics sinks next to events_<scenario>.csv.
        # Suffix matches process_result.scenario_name (config.name) for alignment.
        run_dir = scenario_logger.get_log_dir()
        flush_decision_diagnostics(
            decision_logic, RunEntryWriter(run_dir, RUN_TYPE_SIMULATION) if run_dir else None,
            scenario_suffix=config.name)

        # === Process Final status ===
        success = True
        error_type = None
        error_message = None
        error_traceback = None

        # === Get Log Buffer ===
        log_buffer = scenario_logger.get_records()
        errors_in_buffer = scenario_logger.get_records(LogLevel.ERROR)
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
            error_type = LOGGED_ERRORS_TYPE
            error_message = f'Scenario logged {len(errors_in_buffer)} ERROR(s)'
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
            execution_time_ms=(time.perf_counter() - start_monotonic) * 1000.0,
            tick_loop_results=tick_loop_results,
            scenario_logger_buffer=log_buffer,
            error_type=error_type,
            error_message=error_message,
            traceback=error_traceback
        )
        return result

    except Exception as e:
        # HARD Error handling: Return error details for logging
        log_buffer = None
        try:
            # try to fetch Log, if possible.
            log_buffer = scenario_logger.get_records()
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


def _open_state_frames(
    config: ProcessScenarioConfig,
    pipeline: ProcessPipelineBundle,
    scenario_logger: ScenarioLogger,
) -> Optional[StateFrameRecorder]:
    """
    Open the scenario's state-frame stream when the scenario asks for one (#579 part B item 3).

    The stream lies in the run's `io/`, one file per scenario, beside its events table. The frames
    describe the simulated account: no reconciler, no circuit breaker, and no memory reading; they
    re-mark no position, so recording them changes nothing the backtest computes.

    Args:
        config: The scenario's configuration — whether it records frames, and on which cadence
        pipeline: The scenario's objects — executor, workers, bars
        scenario_logger: The scenario's logger, which knows the run directory

    Returns:
        The recorder, or None when the scenario records no frames
    """
    if not config.state_frames_enabled:
        return None
    run_dir = scenario_logger.get_log_dir()
    if not run_dir:
        scenario_logger.warning(
            '📼 State frames were asked for, but this scenario writes into no run directory — '
            'none are recorded')
        return None
    appender = RunEntryWriter(run_dir, RUN_TYPE_SIMULATION).open_stream(
        RunEntryMemberId.SCENARIO_STATE_FRAMES, config.run_id, scenario_logger,
        stops="The scenario's state is no longer recorded",
        header_fields={'unit_name': config.name,
                       'cadence_timeframe': config.state_frames_cadence,
                       'cadence_s': TimeframeConfig.get_minutes(config.state_frames_cadence) * 60.0},
        scenario=config.name)
    scenario_logger.info(
        f'📼 State frames: one per {config.state_frames_cadence} period of the simulated clock, '
        f'at a closed bar and at an order → {appender.get_path()}')
    return StateFrameRecorder(
        appender, config.state_frames_cadence, config.symbol, pipeline.trade_simulator,
        pipeline.worker_coordinator, pipeline.bar_rendering_controller, config.trading_model,
        replayed=False, logger=scenario_logger, memory_reader=None, refresh_positions=False)
