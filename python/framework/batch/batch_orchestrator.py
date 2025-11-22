"""
FiniexTestingIDE - Batch Orchestrator ()
Universal entry point for 1-1000+ test scenarios

"""

# ==============================================================================
# PROCESSPOOL vs THREADPOOL - ARCHITECTURAL NOTES
# ==============================================================================
"""
This orchestrator supports both ThreadPoolExecutor and ProcessPoolExecutor for
parallel scenario execution. Each has important trade-offs:

┌─────────────────────────────────────────────────────────────────────────────┐
│ THREADPOOL (ThreadPoolExecutor)                                             │
├─────────────────────────────────────────────────────────────────────────────┤
│ ✅ Pros:                                                                     │
│    • Fast startup (no process fork overhead)                                │
│    • Instant shutdown (<10ms)                                               │
│    • Works seamlessly with debuggers (VSCode, PyCharm)                      │
│    • No file handle inheritance issues                                      │
│                                                                              │
│ ❌ Cons:                                                                     │
│    • Limited by Python GIL (Global Interpreter Lock)                        │
│    • No true parallelism for CPU-bound tasks                                │
│    • Slower for 10+ scenarios (quasi-sequential due to GIL)                 │
│                                                                              │
│ 📊 Performance (3 scenarios @ 3.5s each):                                   │
│    Total: ~12s (GIL contention prevents true parallel execution)            │
│                                                                              │
│ 🎯 Best for:                                                                │
│    • Development with debugger attached                                     │
│    • Small batches (1-5 scenarios)                                          │
│    • Quick testing                                                          │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│ PROCESSPOOL (ProcessPoolExecutor)                                           │
├─────────────────────────────────────────────────────────────────────────────┤
│ ✅ Pros:                                                                     │
│    • TRUE parallelism (no GIL limitations)                                  │
│    • 3-4x faster for large batches (10+ scenarios)                          │
│    • Optimal CPU utilization                                                │
│                                                                              │
│ ❌ Cons:                                                                     │
│    • Requires careful resource cleanup (file handles, logging)              │
│    • Slower startup (process fork overhead ~50-100ms per worker)            │
│    • Debugger issues (VSCode debugpy inherits file handles)                 │
│    • Shutdown may take 50-100ms (normal!) or 10+ seconds (bug!)            │
│                                                                              │
│ 📊 Performance (3 scenarios @ 3.5s each):                                   │
│    Total: ~4-5s (true parallel execution)                                   │
│                                                                              │
│ 🎯 Best for:                                                                │
│    • Production runs without debugger                                       │
│    • Large batches (10-1000+ scenarios)                                     │
│    • Maximum performance                                                    │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│ ⚠️  CRITICAL: ProcessPool Cleanup Requirements                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│ ProcessPool uses fork() on Linux, which copies the entire process memory    │
│ including ALL open file handles and sockets. If these aren't closed         │
│ properly, Python waits for them to timeout (~10 seconds) during shutdown.   │
│                                                                              │
│ REQUIRED CLEANUPS (implemented in process_executor.py):                     │
│   1. FileLogger.close()        - Close scenario log files                   │
│   2. coordinator.cleanup()     - Close ThreadPool (if enabled)              │
│   3. logging.shutdown()        - Close ALL Python logging handlers          │
│                                                                              │
│ DEBUGGER COMPATIBILITY:                                                     │
│   ⚠️  VSCode debugpy creates sockets that are inherited by fork()           │
│   ⚠️  These sockets cause 10+ second shutdown delays                        │
│                                                                              │
│   Solutions:                                                                │
│   • Run without debugger: python python/strategy_runner.py                  │
│   • OR use forkserver: multiprocessing.set_start_method('forkserver')       │
│   • OR use ThreadPool for debugging (slower but works)                      │
│                                                                              │
│ STARTUP METHODS:                                                            │
│   • fork       - Fast, but inherits everything (Linux default)              │
│   • spawn      - Clean, but very slow startup (~1s per process)             │
│   • forkserver - Compromise: clean fork from dedicated server               │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│ 🎚️  CONFIGURATION SWITCH                                                    │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│ Change USE_PROCESSPOOL in _run_parallel() to switch between modes:          │
│                                                                              │
│   USE_PROCESSPOOL = True   # ProcessPool - best performance                │
│   USE_PROCESSPOOL = False  # ThreadPool  - best compatibility              │
│                                                                              │
│ The code automatically handles all differences between the two modes.       │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘

PERFORMANCE COMPARISON (3 scenarios @ 3.5s each):
┌──────────────────┬────────────────┬──────────────┬──────────────┐
│ Mode             │ Parallelism    │ Shutdown     │ Total Time   │
├──────────────────┼────────────────┼──────────────┼──────────────┤
│ ProcessPool*     │ ✅ True        │ ~50ms        │ ~4-5s   🏆   │
│ ThreadPool       │ ❌ GIL-limited │ <10ms        │ ~12s         │
│ Sequential       │ ❌ None        │ N/A          │ ~10.5s       │
└──────────────────┴────────────────┴──────────────┴──────────────┘
* Without debugger attached

RECOMMENDATION:
- Development: Use ThreadPool (debugging support)
- Production:  Use ProcessPool (maximum performance)
- Switch with one line: USE_PROCESSPOOL = True/False
"""
import time
from typing import Dict, List
from multiprocessing import Manager
from python.components.logger.abstract_logger import AbstractLogger
from python.framework.exceptions.scenario_execution_errors import BatchExecutionError
from python.configuration import AppConfigManager
from python.framework.types.process_data_types import ProcessDataPackage, ProcessResult
from python.framework.types.scenario_set_types import ScenarioSet
from python.framework.types.live_stats_config_types import LiveStatsExportConfig, ScenarioStatus
from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.factory.decision_logic_factory import DecisionLogicFactory
from python.framework.factory.worker_factory import WorkerFactory
from python.components.display.live_progress_display import LiveProgressDisplay
from python.framework.batch.live_stats_coordinator import LiveStatsCoordinator
from python.framework.batch.execution_coordinator import ExecutionCoordinator
from python.framework.batch.requirements_collector import RequirementsCollector
from python.framework.batch.data_preparation_coordinator import DataPreparationCoordinator


class BatchOrchestrator:
    """
    Universal orchestrator for batch strategy testing.
    Handles 1 to 1000+ scenarios with same code path.

    Key improvement: Failed preparations don't block execution
    - Only successfully prepared scenarios wait at barrier
    - Failed scenarios are logged but don't stop batch
    """

    def __init__(
        self,
        scenario_set: ScenarioSet,
        app_config_loader: AppConfigManager
    ):
        """
        Initialize batch orchestrator.

        Creates run_timestamp for shared logger initialization.

        Args:
            scenario_set: Set of scenarios to execute
            app_config_loader: Application configuration manager
        """
        self.scenario_set = scenario_set
        self._app_config_loader = app_config_loader
        self._parallel_scenarios = app_config_loader.get_default_parallel_scenarios()

        # Start global log
        self.scenario_set.logger.reset_start_time()
        self.scenario_set.logger.info(
            "🚀 Starting Scenario " +
            self.scenario_set.scenario_set_name +
            " Log Timer (Batch Init)."
        )

        # Initialize factories
        self.worker_factory = WorkerFactory(logger=self.scenario_set.logger)
        self.decision_logic_factory = DecisionLogicFactory(
            logger=self.scenario_set.logger
        )

        # Shared data package (filled in run())
        self.shared_data: ProcessDataPackage = None

        # Extract scenario_set_name from scenario_set
        self.scenario_set_name = self.scenario_set.scenario_set_name

        # Create shared run_timestamp for all processes
        self.logger_start_time_format = self.scenario_set.logger.get_run_timestamp()

        # Live stats config
        self.live_stats_config = LiveStatsExportConfig.from_app_config(
            self._app_config_loader.get_config(),
            len(scenario_set.scenarios)
        )

        # Create queue (if monitoring enabled)
        if self.live_stats_config.enabled:
            self._manager = Manager()
            self._live_queue = self._manager.Queue(maxsize=100)
        else:
            self._manager = None
            self._live_queue = None

        # Initialize coordinators
        self._requirements_collector = RequirementsCollector(
            logger=self.scenario_set.logger
        )

        self._data_coordinator = DataPreparationCoordinator(
            scenarios=scenario_set.scenarios,
            logger=self.scenario_set.logger
        )

        self._execution_coordinator = ExecutionCoordinator(
            scenario_set_name=self.scenario_set_name,
            run_timestamp=self.logger_start_time_format,
            app_config=self._app_config_loader,
            live_stats_config=self.live_stats_config,
            logger=self.scenario_set.logger
        )

        self._live_stats_coordinator = LiveStatsCoordinator(
            scenarios=scenario_set.scenarios,
            live_queue=self._live_queue,
            enabled=self.live_stats_config.enabled
        )

        # Create display (if monitoring enabled)
        if self.live_stats_config.enabled:
            self._display = LiveProgressDisplay(
                scenarios=scenario_set.scenarios,
                live_queue=self._live_queue,
                update_interval=self.live_stats_config.update_interval_sec
            )
        else:
            self._display = None

        # Log live stats setup
        if self.live_stats_config.enabled:
            mode = "DETAILED" if self.live_stats_config.detailed_mode else "BASIC"
            self.scenario_set.logger.info(
                f"📊 Live stats: {mode} mode "
                f"(update interval: {self.live_stats_config.update_interval_sec:.2f}s)"
            )
            if self.live_stats_config.detailed_mode:
                self.scenario_set.logger.debug(
                    f"   Exports: Portfolio={self.live_stats_config.export_portfolio_stats}, "
                    f"Performance={self.live_stats_config.export_performance_stats}, "
                    f"Bars={self.live_stats_config.export_current_bars}"
                )

        self.scenario_set.logger.debug(
            f"📦 BatchOrchestrator initialized: "
            f"scenario_set='{self.scenario_set_name}', "
            f"run_timestamp='{self.logger_start_time_format}', "
            f"{len(scenario_set.scenarios)} scenario(s)"
        )

    def run(self) -> BatchExecutionSummary:
        """
        Execute all scenarios with coordinated phases.

        WORKFLOW:
        Phase 0: Requirements Collection (Serial)
        Phase 1: Data Preparation (Serial)
        Phase 2: Scenario Execution (Parallel/Sequential)

        Returns:
            BatchExecutionSummary with aggregated results from all scenarios
        """
        scenario_count = len(self.scenario_set.scenarios)
        force_sequential = scenario_count == 1

        if force_sequential:
            self.scenario_set.logger.info(
                "⚠️ Sequential execution forced - only one scenario in set."
            )

        self.scenario_set.logger.info(
            f"🚀 Starting batch execution "
            f"({scenario_count} scenarios, "
            f"run_timestamp={self.logger_start_time_format})"
        )

        start_time = time.time()
        self._live_stats_coordinator.broadcast_status(
            ScenarioStatus.INITIALIZED)

        # Start live display
        if self._display:
            self._display.start()

        # ========================================================================
        # PHASE 0: REQUIREMENTS COLLECTION (Serial)
        # ========================================================================
        requirements_map, warmup_reqs = self._requirements_collector.collect(
            scenarios=self.scenario_set.scenarios,
            app_config=self._app_config_loader
        )

        # ========================================================================
        # PHASE 1: DATA PREPARATION (Serial)
        # ========================================================================
        self.shared_data = self._data_coordinator.prepare(
            requirements_map=requirements_map,
            status_broadcaster=self._live_stats_coordinator
        )

        # ========================================================================
        # PHASE 2: SCENARIO EXECUTION (Parallel/Sequential)
        # ========================================================================
        self.scenario_set.logger.info("🚦 Phase 2: Executing scenarios...")

        if self._parallel_scenarios and scenario_count > 1:
            results = self._execution_coordinator.execute_parallel(
                scenarios=self.scenario_set.scenarios,
                shared_data=self.shared_data,
                live_queue=self._live_queue
            )
        else:
            results = self._execution_coordinator.execute_sequential(
                scenarios=self.scenario_set.scenarios,
                shared_data=self.shared_data,
                live_queue=self._live_queue
            )

        summary_execution_time = time.time() - start_time

        # ========================================================================
        # CLEANUP
        # ========================================================================
        if self._display and self._display._running:
            self._display.stop()
        if self._manager:
            try:
                self._manager.shutdown()
            except:
                pass

        # ========================================================================
        # PHASE 3: BUILD SUMMARY
        # ========================================================================
        self.scenario_set.logger.info(
            f"🕐 Create BatchExecutionSummary : {time.time()}"
        )

        summary = BatchExecutionSummary(
            success=True,
            scenarios_count=len(self.scenario_set.scenarios),
            summary_execution_time=summary_execution_time,
            broker_scenario_map=self._data_coordinator.get_broker_scenario_map(),
            scenario_list=results
        )

        self.flush_all_logs(summary)

        # Error handling
        self.scenario_set.logger.info(
            f"🕐 Scenario error check : {time.time()}"
        )

        failed_results = [r for r in results if not r.success]
        if failed_results:
            # Log failures but don't stop (scenarios are independent)
            for failed in failed_results:
                self.scenario_set.logger.error(
                    f"❌ Scenario failed - flushing Log: "
                    f"{failed.scenario_name} - {failed.error_message}"
                )
                self.scenario_set.logger.flush_buffer()

            # After all processing, raise comprehensive error
            raise BatchExecutionError(failed_results)

        self.scenario_set.logger.info(
            f"✅ Batch execution completed in {summary_execution_time:.2f}s"
        )

        return summary

    def flush_all_logs(self, batch_execution_summary: BatchExecutionSummary = None):
        """
        Logger flush. Run does not decide whether to console log.

        Args:
            batch_execution_summary: Summary with scenario results
        """
        self.scenario_set.logger.close(True)
        show_scenario_logging = self._app_config_loader.get_logging_show_scenario_logging()

        if show_scenario_logging:
            for process_result in batch_execution_summary.scenario_list:
                AbstractLogger.print_buffer(
                    process_result.scenario_logger_buffer,
                    process_result.scenario_name
                )
