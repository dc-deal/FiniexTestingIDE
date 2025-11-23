"""
FiniexTestingIDE - Execution Coordinator
Phase 2: Coordinates sequential and parallel scenario execution

Extracted from BatchOrchestrator to separate execution logic.
"""
from python.framework.process.process_executor import ProcessExecutor
from python.framework.process.process_live_queue_helper import broadcast_status_update
from python.framework.process.process_main import process_main
from python.framework.types.scenario_set_types import SingleScenario
from python.framework.types.process_data_types import ProcessDataPackage, ProcessResult
from python.framework.types.live_stats_config_types import LiveStatsExportConfig, ScenarioStatus
from python.configuration import AppConfigManager
from python.components.logger.abstract_logger import AbstractLogger
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from multiprocessing import Queue
from typing import List, Optional
import os
import sys
import time
import traceback


# Auto-detect if debugger is attached
DEBUGGER_ACTIVE = (
    hasattr(sys, 'gettrace') and sys.gettrace() is not None
    or 'debugpy' in sys.modules
    or 'pydevd' in sys.modules
)


class ExecutionCoordinator:
    """
    Coordinates scenario execution (sequential or parallel).

    Responsibilities:
    - Execute scenarios sequentially or in parallel
    - Auto-detect debugger and switch execution mode
    - Handle ProcessPoolExecutor vs ThreadPoolExecutor
    - Collect and return execution results
    """

    def __init__(
        self,
        scenario_set_name: str,
        run_timestamp: str,
        app_config: AppConfigManager,
        live_stats_config: LiveStatsExportConfig,
        logger: AbstractLogger
    ):
        """
        Initialize execution coordinator.

        Args:
            scenario_set_name: Name of the scenario set
            run_timestamp: Timestamp for this batch run
            app_config: Application configuration manager
            live_stats_config: Live stats configuration
            logger: Logger instance for status messages
        """
        self._scenario_set_name = scenario_set_name
        self._run_timestamp = run_timestamp
        self._app_config = app_config
        self._live_stats_config = live_stats_config
        self._logger = logger

    def execute_sequential(
        self,
        scenarios: List[SingleScenario],
        shared_data: ProcessDataPackage,
        live_queue: Optional[Queue]
    ) -> List[ProcessResult]:
        """
        Execute scenarios sequentially.

        Args:
            scenarios: List of scenarios to execute
            shared_data: Prepared shared data package
            live_queue: Optional queue for live updates

        Returns:
            List of ProcessResult objects
        """
        results = []

        for idx, scenario in enumerate(scenarios):
            readable_index = idx + 1

            # === CHECK VALIDATION STATUS (NEW) ===
            if not scenario.is_valid():
                self._logger.warning(
                    f"⚠️  Scenario {readable_index}/{len(scenarios)}: "
                    f"{scenario.name} - SKIPPED (validation failed)"
                )

                # Create failed result
                failed_result = self._create_validation_failed_result(
                    scenario, idx)
                results.append(failed_result)

                # Broadcast FAILED status to display
                broadcast_status_update(live_queue=live_queue,
                                        scenario_index=idx,
                                        scenario_name=scenario.name,
                                        status=ScenarioStatus.FINISHED_WITH_ERROR,
                                        live_stats_config=self._live_stats_config
                                        )

                continue  # Skip to next scenario

            self._logger.info(
                f"▶️  Executing scenario {readable_index}/{len(scenarios)}: "
                f"{scenario.name}"
            )

            # Create executor
            executor = ProcessExecutor(
                scenario=scenario,
                app_config_loader=self._app_config,
                scenario_index=idx,
                scenario_set_name=self._scenario_set_name,
                run_timestamp=self._run_timestamp,
                live_stats_config=self._live_stats_config
            )

            # Execute
            result = executor.run(shared_data, live_queue)
            results.append(result)

            if result.success:
                self._logger.info(
                    f"✅ Scenario {readable_index} completed in "
                    f"{result.execution_time_ms:.0f}ms"
                )
            else:
                self._logger.error(
                    f"❌ Scenario {readable_index} failed: {result.error_message}"
                )

        return results

    def execute_parallel(
        self,
        scenarios: List[SingleScenario],
        shared_data: ProcessDataPackage,
        live_queue: Optional[Queue]
    ) -> List[ProcessResult]:
        """
        Execute scenarios in parallel with auto-detection.

        Automatically switches between ProcessPoolExecutor and ThreadPoolExecutor
        based on debugger detection.

        Args:
            scenarios: List of scenarios to execute
            shared_data: Prepared shared data package
            live_queue: Optional queue for live updates

        Returns:
            List of ProcessResult objects
        """
        # Auto-switch based on environment
        if DEBUGGER_ACTIVE or os.getenv('DEBUG_MODE'):
            use_processpool = False
            self._logger.warning(
                "⚠️  Debugger detected - using ThreadPool "
                "(performance not representative!)"
            )
        else:
            use_processpool = True
            self._logger.info("🚀 Performance mode - using ProcessPool")

        executor_class = ProcessPoolExecutor if use_processpool else ThreadPoolExecutor
        max_workers = self._app_config.get_default_max_parallel_scenarios()

        self._logger.info(
            f"🔀 Parallel execution: {executor_class.__name__} "
            f"(max_workers={max_workers})"
        )

        results = [None] * len(scenarios)

        with executor_class(max_workers=max_workers) as executor:
            # Submit all scenarios
            futures = {}
            for idx, scenario in enumerate(scenarios):

                # === CHECK VALIDATION STATUS (NEW) ===
                if not scenario.is_valid():
                    readable_index = idx + 1
                    self._logger.warning(
                        f"⚠️  Scenario {readable_index}: {scenario.name} - "
                        f"SKIPPED (validation failed)"
                    )

                    # Create failed result immediately
                    results[idx] = self._create_validation_failed_result(
                        scenario, idx)

                    # Broadcast FAILED status to display
                    broadcast_status_update(live_queue=live_queue,
                                            scenario_index=idx,
                                            scenario_name=scenario.name,
                                            status=ScenarioStatus.FINISHED_WITH_ERROR,
                                            live_stats_config=self._live_stats_config
                                            )

                    continue

                # Create executor config
                executor_obj = ProcessExecutor(
                    scenario=scenario,
                    app_config_loader=self._app_config,
                    scenario_index=idx,
                    scenario_set_name=self._scenario_set_name,
                    run_timestamp=self._run_timestamp,
                    live_stats_config=self._live_stats_config
                )

                # Submit to executor
                future = executor.submit(
                    process_main,
                    executor_obj.config,
                    shared_data,
                    live_queue
                )
                futures[future] = idx

            # Collect results
            for future in as_completed(futures):
                idx = futures[future]
                readable_index = idx + 1

                try:
                    result = future.result()
                    results[idx] = result

                    if result.success:
                        self._logger.info(
                            f"✅ Scenario {readable_index} completed: "
                            f"{result.scenario_name} ({result.execution_time_ms:.0f}ms)"
                        )
                    else:
                        self._logger.error(
                            f"❌ Scenario {readable_index} failed: "
                            f"{result.scenario_name} - {result.error_message}"
                        )

                except Exception as e:
                    # Unexpected error (not caught in process_main)
                    self._logger.error(
                        f"❌ Scenario {readable_index} crashed: "
                        f"\n{traceback.format_exc()}"
                    )
                    results[idx] = ProcessResult(
                        success=False,
                        scenario_name=scenarios[idx].name,
                        symbol=scenarios[idx].symbol,
                        scenario_index=idx,
                        error_type=type(e).__name__,
                        error_message=str(e),
                        traceback=traceback.format_exc()
                    )

            self._logger.info(
                "🕐 All futures collected, exiting context manager..."
            )
            self._logger.info(
                "🕐 If a major slowdown occurs here, " +
                "it's just the debugger who waits for processes." +
                " You can't skip this..."
            )

        self._logger.info(
            f"🕐 ProcessPoolExecutor shutdown complete! Time: {time.time()}"
        )

        return results

    def _create_validation_failed_result(
        self,
        scenario: SingleScenario,
        scenario_index: int
    ) -> ProcessResult:
        """
        Create ProcessResult for validation-failed scenario.

        Args:
            scenario: Scenario that failed validation
            scenario_index: Index in scenario list

        Returns:
            ProcessResult with validation error details
        """
        validation_result = scenario.validation_result

        return ProcessResult(
            success=False,
            scenario_name=scenario.name,
            symbol=scenario.symbol,
            scenario_index=scenario_index,
            error_type="ValidationError",
            error_message=validation_result.get_full_report(
            ) if validation_result else "Unknown validation error",
            traceback=None,  # No traceback for validation errors
            execution_time_ms=0.0
        )
