"""
FiniexTestingIDE - Execution Coordinator
Phase 2: Coordinates sequential and parallel scenario execution

Extracted from BatchOrchestrator to separate execution logic.
"""
import pickle
from python.framework.process.process_executor import ProcessExecutor
from python.framework.process.process_live_queue_helper import broadcast_status_update
from python.framework.process.process_main import process_main
from python.framework.types.scenario_set_types import SingleScenario
from python.framework.types.process_data_types import ProcessDataPackage, ProcessResult
from python.framework.types.live_stats_config_types import LiveStatsExportConfig, ScenarioStatus
from python.configuration.app_config_manager import AppConfigManager
from python.framework.logging.abstract_logger import AbstractLogger
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from multiprocessing import Queue
from typing import Dict, List, Optional
import os
import sys
import time
import traceback

from python.framework.types.validation_types import ValidationResult, get_validation_list_report


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
        scenario_packages: Dict[int, ProcessDataPackage],
        live_queue: Optional[Queue]
    ) -> List[ProcessResult]:
        """
        Execute scenarios sequentially.

        Args:
            scenarios: List of scenarios to execute
            scenario_packages: Dict mapping scenario_index → ProcessDataPackage
            live_queue: Optional queue for live updates

        Returns:
            List of ProcessResult objects
        """
        results = [None] * len(scenarios)

        for idx, scenario in enumerate(scenarios):
            readable_index = idx + 1

            # === CHECK VALIDATION STATUS ===
            if not scenario.is_valid():
                # Create failed result immediately
                results[idx] = self._create_validation_failed_result(
                    scenario, idx, live_queue)
                continue

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

            # === Use scenario-specific package ===
            scenario_data = scenario_packages.get(idx)
            if scenario_data is None:
                # Should never happen if validation passed
                results[idx] = self._create_validation_failed_result(
                    scenario, idx, live_queue,  f"❌ No data package for scenario {idx}: {scenario.name} - data packages: {len(scenario_packages)}")
                continue

            # Execute with scenario-specific data
            # Changed: scenario_data
            results[idx] = executor.run(scenario_data, live_queue)

            if results[idx].success:
                self._logger.info(
                    f"✅ Scenario {readable_index} completed in "
                    f"{results[idx].execution_time_ms:.0f}ms"
                )
            else:
                self._logger.error(
                    f"❌ Scenario {readable_index} failed: {results[idx].error_message}"
                )

        return results

    def execute_parallel(
        self,
        scenarios: List[SingleScenario],
        scenario_packages: Dict[int, ProcessDataPackage],
        live_queue: Optional[Queue]
    ) -> List[ProcessResult]:
        """
        Execute scenarios in parallel with auto-detection.

        OPTIMIZATION: Each scenario receives only its required data (3-5 MB)
        instead of global package (61 MB). Reduces pickle time by 5x.

        Args:
            scenarios: List of scenarios to execute
            scenario_packages: Dict mapping scenario_index → ProcessDataPackage
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

                # === CHECK VALIDATION STATUS ===
                if not scenario.is_valid():
                    # Create failed result immediately
                    results[idx] = self._create_validation_failed_result(
                        scenario, idx, live_queue)
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

                # === Use scenario-specific package ===
                scenario_data = scenario_packages.get(idx)
                if scenario_data is None:
                    results[idx] = self._create_validation_failed_result(
                        scenario, idx, live_queue,  f"❌ No data package for scenario {idx}: {scenario.name} - data packages: {len(scenario_packages)}")
                    continue

                # pickle time measurement: Check how long it takes to shovel the object data via serialization into the sub process...
                # USE WITH CAUTION!! This is a DEBUG Test which slows down Pickle time (we do it twice - one for the measurement, one later as to fill the subProcess)
                # pickle_start = time.time()
                # pickled = pickle.dumps((executor_obj.config, scenario_data))
                # pickle_time = time.time() - pickle_start
                # self._logger.info(
                #     f"⏱️  Pickle time: {pickle_time:.2f}s, Size: {len(pickled)/1024/1024:.1f} MB")

                # Submit to executor with scenario-specific data
                future = executor.submit(
                    process_main,
                    executor_obj.config,
                    # Changed: scenario-specific package (~3-5 MB)
                    scenario_data,
                    live_queue
                )
                futures[future] = idx

            # Collect results (unchanged)
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
        scenario_index: int,
        live_queue: Optional[Queue],
        additional_error_message: Optional[str] = None
    ) -> ProcessResult:
        """
        Create ProcessResult for validation-failed scenario.

        Args:
            scenario: Scenario that failed validation
            scenario_index: Index in scenario list

        Returns:
            ProcessResult with validation error details
        """

        readable_index = scenario_index + 1
        self._logger.warning(
            f"⚠️  Scenario {readable_index}: {scenario.name} - "
            f"SKIPPED (validation failed)"
        )

        # Broadcast FAILED status to display
        broadcast_status_update(live_queue=live_queue,
                                scenario_index=scenario_index,
                                scenario_name=scenario.name,
                                status=ScenarioStatus.FINISHED_WITH_ERROR,
                                live_stats_config=self._live_stats_config
                                )

        # append an additional error, if nessecary (commonly used right before execution)
        if (additional_error_message is not None):
            self._logger.error(additional_error_message)
            validation_error = ValidationResult(
                is_valid=False,
                scenario_name=scenario.name,
                errors=[additional_error_message],
                warnings=[]
            )
            scenario.validation_result.append(validation_error)

        validation_result = scenario.validation_result
        return ProcessResult(
            success=False,
            scenario_name=scenario.name,
            scenario_index=scenario_index,
            error_type="ValidationError",
            error_message=get_validation_list_report(validation_result),
            traceback=None,  # No traceback for validation errors
            execution_time_ms=0.0
        )
