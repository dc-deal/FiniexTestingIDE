"""
FiniexTestingIDE - Batch Orchestrator (REFACTORED)
Universal entry point for 1-1000+ test scenarios

REFACTORED: Clean separation of concerns
- BatchOrchestrator manages parallelization and coordination
- ScenarioExecutor handles individual scenario execution
- Barrier synchronization only for successfully prepared scenarios
"""

import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List

from python.components.logger.bootstrap_logger import get_logger
from python.data_worker.data_loader.core import TickDataLoader
from python.framework.scenario_executor import ScenarioExecutor
from python.framework.types.global_types import BatchExecutionSummary, TestScenario
from python.framework.types.live_stats_types import ScenarioStatus
from python.framework.types.scenario_types import (
    ScenarioExecutorDependencies,
    ScenarioExecutionResult
)
from python.framework.reporting.scenario_set_performance_manager import (
    ScenarioSetPerformanceManager
)
from python.configuration import AppConfigLoader
from python.framework.exceptions.scenario_execution_errors import (
    ScenarioPreparationError,
    ScenarioExecutionError
)

# Factory Imports
from python.framework.factory.worker_factory import WorkerFactory
from python.framework.factory.decision_logic_factory import DecisionLogicFactory

# NEW (Phase 1a): Live Progress Display
from python.components.display.live_progress_display import LiveProgressDisplay

vLog = get_logger()


class BatchOrchestrator:
    """
    Universal orchestrator for batch strategy testing.
    Handles 1 to 1000+ scenarios with same code path.

    REFACTORED: Clean architecture
    - Manages parallel/sequential execution
    - Creates ScenarioExecutor for each scenario
    - Handles barrier synchronization for parallel mode
    - Collects and aggregates results

    Key improvement: Failed preparations don't block execution
    - Only successfully prepared scenarios wait at barrier
    - Failed scenarios are logged but don't stop batch
    """

    def __init__(
        self,
        scenarios: List[TestScenario],
        data_worker: TickDataLoader,
        app_config: AppConfigLoader,
        performance_log: ScenarioSetPerformanceManager,
    ):
        """
        Initialize batch orchestrator.

        Args:
            scenarios: List of test scenarios
            data_worker: TickDataLoader instance
            app_config: Application configuration
            performance_log: Statistics collection container
        """
        self.scenarios = scenarios
        self.data_worker = data_worker
        self.appConfig = app_config
        self.performance_log = performance_log

        # Initialize Factories
        self.worker_factory = WorkerFactory()
        self.decision_logic_factory = DecisionLogicFactory()

        # Create dependency container for ScenarioExecutor
        self.dependencies = ScenarioExecutorDependencies(
            data_worker=data_worker,
            app_config=app_config,
            performance_log=performance_log,
            worker_factory=self.worker_factory,
            decision_logic_factory=self.decision_logic_factory
        )

        # Track last executor for debugging
        self._last_executor = None

        vLog.debug(
            f"📦 BatchOrchestrator initialized with {len(scenarios)} scenario(s)"
        )

    def run(self) -> BatchExecutionSummary:
        """
        Execute all scenarios.

        Returns:
            Aggregated results from all scenarios
        """
        vLog.info(
            f"🚀 Starting batch execution ({len(self.scenarios)} scenarios)"
        )
        start_time = time.time()

        # Get batch mode from app_config.json
        run_parallel = self.appConfig.get_default_parallel_scenarios()

        # Execute scenarios
        if run_parallel and len(self.scenarios) > 1:
            results = self._run_parallel()
        else:
            results = self._run_sequential()

        # Aggregate results
        summary_execution_time = time.time() - start_time

        # Set metadata in ScenarioSetPerformanceManager
        self.performance_log.set_metadata(
            summary_execution_time=summary_execution_time,
            success=True
        )

        summary = BatchExecutionSummary(
            success=True,
            scenarios_count=len(self.scenarios),
            summary_execution_time=summary_execution_time
        )

        vLog.debug(
            f"✅ Batch execution completed in {summary_execution_time:.2f}s")
        return summary

    def _run_sequential(self) -> List[ScenarioExecutionResult]:
        """
        Execute scenarios sequentially (easier debugging).

        In sequential mode:
        - No barrier needed (scenarios run one by one)
        - Preparation failures stop that scenario but continue batch
        - Live display shows progress
        """
        # ===== Phase 1a: Setup Live Display =====
        live_display = LiveProgressDisplay(
            self.performance_log,
            self.scenarios
        )
        live_display.start()

        # ===== Execute Scenarios =====
        results = []

        for scenario_index, scenario in enumerate(self.scenarios):
            readable_index = scenario_index + 1

            try:
                # Create executor for this scenario
                executor = ScenarioExecutor(self.dependencies)
                self._last_executor = executor

                # Execute without barrier (sequential)
                result = executor.execute(
                    scenario=scenario,
                    scenario_index=scenario_index,
                    barrier=None
                )
                results.append(result)

            except ScenarioPreparationError as e:
                vLog.error(
                    f"❌ Scenario {readable_index} preparation failed: {str(e)}"
                )
                self.performance_log.set_live_status(
                    scenario_index=scenario_index,
                    status=ScenarioStatus.FINISHED_WITH_ERROR
                )
                results.append(
                    ScenarioExecutionResult(
                        success=False,
                        scenario_name=scenario.name,
                        scenario_index=scenario_index,
                        error=str(e)
                    )
                )

            except ScenarioExecutionError as e:
                vLog.error(
                    f"❌ Scenario {readable_index} execution failed: {str(e)}"
                )
                self.performance_log.set_live_status(
                    scenario_index=scenario_index,
                    status=ScenarioStatus.FINISHED_WITH_ERROR
                )
                results.append(
                    ScenarioExecutionResult(
                        success=False,
                        scenario_name=scenario.name,
                        scenario_index=scenario_index,
                        error=str(e)
                    )
                )

            except Exception as e:
                vLog.error(
                    f"❌ Scenario {readable_index} failed: \n{traceback.format_exc()}"
                )
                self.performance_log.set_live_status(
                    scenario_index=scenario_index,
                    status=ScenarioStatus.FINISHED_WITH_ERROR
                )
                results.append(
                    ScenarioExecutionResult(
                        success=False,
                        scenario_name=scenario.name,
                        scenario_index=scenario_index,
                        error=str(e)
                    )
                )

        # ===== Phase 1a: Cleanup =====
        live_display.stop()
        for scenario in self.scenarios:
            scenario.logger.flush_buffer()

        return results

    def _run_parallel(self) -> List[ScenarioExecutionResult]:
        """
        Execute scenarios in parallel using threads.

        CRITICAL: Barrier only for successfully prepared scenarios
        - All scenarios attempt preparation independently
        - Only successful preparations proceed to tick loop
        - Barrier created AFTER preparation phase
        - Failed preparations don't block successful ones

        Process:
        1. Prepare all scenarios in parallel (with error handling)
        2. Create barrier for successfully prepared scenarios
        3. Execute tick loops for successful scenarios (synchronized)
        4. Return results for all scenarios (success + failures)
        """
        # ===== Phase 1a: Setup Live Display =====
        live_display = LiveProgressDisplay(
            self.performance_log,
            self.scenarios
        )
        live_display.start()

        max_workers = self.appConfig.get_default_max_parallel_scenarios()

        # Results array (maintains order)
        results = [None] * len(self.scenarios)

        # Track successful preparations
        successful_executors = []  # List of (executor, scenario_index)

        # ===== PHASE 1: Parallel Preparation =====
        vLog.info("📋 Phase 1: Preparing all scenarios in parallel...")

        with ThreadPoolExecutor(max_workers=max_workers) as executor_pool:
            # Submit all preparation tasks
            future_to_index = {
                executor_pool.submit(
                    self._prepare_scenario_safe,
                    scenario,
                    idx
                ): idx
                for idx, scenario in enumerate(self.scenarios)
            }

            # Collect preparation results
            for future in future_to_index:
                idx = future_to_index[future]
                readable_index = idx + 1

                try:
                    executor = future.result()
                    if executor is not None:
                        # Preparation successful
                        successful_executors.append((executor, idx))
                        vLog.debug(
                            f"✓ Scenario {readable_index} prepared successfully"
                        )
                    else:
                        # Preparation failed (already logged in _prepare_scenario_safe)
                        results[idx] = ScenarioExecutionResult(
                            success=False,
                            scenario_name=self.scenarios[idx].name,
                            scenario_index=idx,
                            error="Preparation failed"
                        )

                except Exception as e:
                    # Unexpected error during preparation
                    vLog.error(
                        f"❌ Scenario {readable_index} preparation error: "
                        f"\n{traceback.format_exc()}"
                    )
                    self.performance_log.set_live_status(
                        scenario_index=idx,
                        status=ScenarioStatus.FINISHED_WITH_ERROR
                    )
                    results[idx] = ScenarioExecutionResult(
                        success=False,
                        scenario_name=self.scenarios[idx].name,
                        scenario_index=idx,
                        error=str(e)
                    )

        # ===== Check if any scenarios prepared successfully =====
        if not successful_executors:
            vLog.error("❌ No scenarios prepared successfully - batch failed")
            live_display.stop()
            for scenario in self.scenarios:
                scenario.logger.flush_buffer()
            return results

        vLog.info(
            f"✅ Phase 1 complete: {len(successful_executors)} of "
            f"{len(self.scenarios)} scenarios prepared successfully"
        )

        # ===== PHASE 2: Synchronized Tick Loop Execution =====
        vLog.info("🚦 Phase 2: Executing tick loops with synchronized start...")

        # Create barrier ONLY for successful scenarios
        num_successful = len(successful_executors)
        barrier = threading.Barrier(
            num_successful,
            action=lambda: vLog.info(
                f"🚦 All {num_successful} scenarios ready - starting synchronized tick processing"
            )
        )

        vLog.debug(
            f"🔒 Created barrier for {num_successful} successfully prepared scenarios"
        )

        # Execute tick loops in parallel with barrier
        with ThreadPoolExecutor(max_workers=max_workers) as executor_pool:
            future_to_index = {
                executor_pool.submit(
                    self._execute_tick_loop_safe,
                    executor,
                    idx,
                    barrier
                ): idx
                for executor, idx in successful_executors
            }

            for future in future_to_index:
                idx = future_to_index[future]
                readable_index = idx + 1

                try:
                    result = future.result()
                    results[idx] = result

                except Exception as e:
                    vLog.error(
                        f"❌ Scenario {readable_index} execution failed: "
                        f"\n{traceback.format_exc()}"
                    )
                    self.performance_log.set_live_status(
                        scenario_index=idx,
                        status=ScenarioStatus.FINISHED_WITH_ERROR
                    )
                    results[idx] = ScenarioExecutionResult(
                        success=False,
                        scenario_name=self.scenarios[idx].name,
                        scenario_index=idx,
                        error=str(e)
                    )

        # ===== Phase 1a: Cleanup =====
        live_display.stop()
        for scenario in self.scenarios:
            scenario.logger.flush_buffer()

        return results

    def _prepare_scenario_safe(
        self,
        scenario: TestScenario,
        scenario_index: int
    ) -> ScenarioExecutor:
        """
        Safely prepare a scenario with error handling.

        Returns ScenarioExecutor if successful, None if failed.
        Logs errors but does not raise to prevent blocking other scenarios.

        Args:
            scenario: TestScenario to prepare
            scenario_index: Index in scenario list

        Returns:
            ScenarioExecutor if successful, None if failed
        """
        try:
            executor = ScenarioExecutor(self.dependencies)
            executor.prepare_scenario(scenario, scenario_index)
            self._last_executor = executor
            return executor

        except ScenarioPreparationError as e:
            readable_index = scenario_index + 1
            vLog.error(
                f"❌ Scenario {readable_index} preparation failed: {str(e)}"
            )
            self.performance_log.set_live_status(
                scenario_index=scenario_index,
                status=ScenarioStatus.FINISHED_WITH_ERROR
            )
            return None

        except Exception as e:
            readable_index = scenario_index + 1
            vLog.error(
                f"❌ Scenario {readable_index} preparation error: "
                f"\n{traceback.format_exc()}"
            )
            self.performance_log.set_live_status(
                scenario_index=scenario_index,
                status=ScenarioStatus.FINISHED_WITH_ERROR
            )
            return None

    def _execute_tick_loop_safe(
        self,
        executor: ScenarioExecutor,
        scenario_index: int,
        barrier: threading.Barrier
    ) -> ScenarioExecutionResult:
        """
        Safely execute tick loop with barrier synchronization.

        Waits at barrier, then executes tick loop.
        Handles barrier errors gracefully.

        Args:
            executor: Prepared ScenarioExecutor
            scenario_index: Index in scenario list
            barrier: Synchronization barrier

        Returns:
            ScenarioExecutionResult

        Raises:
            Exception: If execution fails (caught by caller)
        """
        readable_index = scenario_index + 1

        # Wait at barrier
        try:
            self.scenarios[scenario_index].logger.debug(
                f"⏸️  Scenario {readable_index} ready - waiting at barrier..."
            )
            barrier.wait(timeout=300)  # 5 minute timeout
            self.scenarios[scenario_index].logger.debug(
                f"🚀 Barrier released - starting tick loop for scenario {readable_index}"
            )

        except threading.BrokenBarrierError:
            self.scenarios[scenario_index].logger.error(
                f"❌ Barrier broken for scenario {readable_index} - "
                f"another scenario failed"
            )
            raise

        except Exception as e:
            self.scenarios[scenario_index].logger.error(
                f"❌ Barrier wait failed for scenario {readable_index}: {e}"
            )
            raise

        # Execute tick loop
        return executor.execute_tick_loop()

    def get_last_executor(self) -> ScenarioExecutor:
        """
        Get last created executor for debugging.

        Returns:
            Last ScenarioExecutor instance
        """
        return self._last_executor
