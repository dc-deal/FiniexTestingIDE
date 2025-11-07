

import time
import traceback
from python.components.logger.scenario_logger import ScenarioLogger
from python.configuration.app_config_loader import AppConfigLoader
from python.framework.process.process_executor import execute_tick_loop
from python.framework.process.process_startup_prepreation import process_startup_preparation
from python.framework.types.process_data_types import ProcessDataPackage, ProcessResult, ProcessScenarioConfig
from python.framework.types.scenario_set_types import SingleScenario


def process_main(
    config: ProcessScenarioConfig,
    shared_data: ProcessDataPackage
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
        # === CREATE SCENARIO LOGGER ===
        # CORRECTED: Use shared run_timestamp from BatchOrchestrator
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

        # === TICK LOOP EXECUTION ===
        tick_loop_results = execute_tick_loop(
            config, prepared_objects)
        scenario_logger.debug(
            f"🔄 Execute tick loop finished")

        # === BUILD RESULT ===
        # logger.run_timestamp - start

        log_buffer = scenario_logger.get_buffer()
        scenario_logger.close()

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
        except:
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


# ============================================================================
# PROCESS EXECUTOR CLASS (Orchestration Wrapper)
# ============================================================================


class ProcessExecutor:
    """
    Orchestrates scenario execution.

    Wrapper around top-level process functions.
    Provides clean interface for BatchOrchestrator.

    DESIGN:
    - Holds scenario and config
    - Calls process_main() (top-level function)
    - Compatible with ThreadPoolExecutor and ProcessPoolExecutor
    """

    def __init__(
        self,
        scenario: SingleScenario,
        app_config: AppConfigLoader,
        scenario_index: int,
        scenario_set_name: str,
        run_timestamp: str
    ):
        """
        Initialize process executor.

        CORRECTED: Added scenario_set_name and run_timestamp

        Args:
            scenario: Scenario to execute
            app_config: Application configuration
            scenario_index: Index in scenario list
            scenario_set_name: Name of scenario set (for logger)
            run_timestamp: Shared timestamp (for logger)
        """
        self.scenario = scenario
        self.app_config = app_config
        self.scenario_index = scenario_index
        self.scenario_set_name = scenario_set_name
        self.run_timestamp = run_timestamp

        # Create config (serializable)
        self.config = ProcessScenarioConfig.from_scenario(
            scenario=scenario,
            app_config=app_config,
            scenario_index=scenario_index,
            scenario_set_name=scenario_set_name,
            run_timestamp=run_timestamp
        )

    def run(self, shared_data: ProcessDataPackage) -> ProcessResult:
        """
        Execute scenario with shared data.

        Entry point for executor. Calls process_main().

        Args:
            shared_data: Prepared shared data

        Returns:
            ProcessResult with execution results
        """
        return process_main(self.config, shared_data)
