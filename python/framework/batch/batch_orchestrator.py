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
from python.framework.validators.scenario_validator import ScenarioValidator
from multiprocessing import Manager
from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.exceptions.scenario_execution_errors import BatchExecutionError
from python.configuration.app_config_manager import AppConfigManager
from python.framework.types.scenario_types.scenario_set_types import ScenarioSet
from python.framework.types.live_types.live_stats_config_types import LiveStatsExportConfig, ScenarioStatus
from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.factory.decision_logic_factory import DecisionLogicFactory
from python.system.ui.live_progress_display import LiveProgressDisplay
from python.framework.batch.live_stats_coordinator import LiveStatsCoordinator
from python.framework.batch.execution_coordinator import ExecutionCoordinator
from python.framework.batch.requirements_collector import RequirementsCollector
from python.framework.discoveries.data_coverage.data_coverage_report_manager import DataCoverageReportManager
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
        app_config_manager: AppConfigManager
    ):
        """
        Initialize batch orchestrator.

        Creates run_timestamp for shared logger initialization.

        Args:
            scenario_set: Set of scenarios to execute
            app_config_manager: Application configuration manager
        """
        self._scenario_set = scenario_set
        self._scenarios = scenario_set.get_all_scenarios()
        self._logger = scenario_set.logger
        self._app_config_manager = app_config_manager
        self._parallel_scenarios = app_config_manager.get_default_parallel_scenarios()

        # Start global log
        self._logger.reset_start_time()
        self._logger.info(
            "🚀 Starting Scenario " +
            self._scenario_set.scenario_set_name +
            " Log Timer (Batch Init)."
        )

        # Initialize factories
        self.decision_logic_factory = DecisionLogicFactory(
            logger=self._logger
        )

        # Extract scenario_set_name from scenario_set
        self.scenario_set_name = self._scenario_set.scenario_set_name

        # Create shared run_timestamp for all processes
        self.logger_start_time_format = self._logger.get_run_timestamp()

        # Live stats config
        self._live_stats_config = LiveStatsExportConfig.from_app_config(
            self._app_config_manager.get_config(),
            len(self._scenarios)
        )

        # Create queue (if monitoring enabled)
        if self._live_stats_config.enabled:
            self._manager = Manager()
            self._live_queue = self._manager.Queue(maxsize=100)
        else:
            self._manager = None
            self._live_queue = None

        # Initialize coordinators
        self._requirements_collector = RequirementsCollector(
            logger=self._logger
        )

        self._execution_coordinator = ExecutionCoordinator(
            scenario_set_name=self.scenario_set_name,
            run_timestamp=self.logger_start_time_format,
            app_config=self._app_config_manager,
            live_stats_config=self._live_stats_config,
            logger=self._logger
        )

        self._live_stats_coordinator = LiveStatsCoordinator(
            scenarios=self._scenarios,
            live_queue=self._live_queue,
            enabled=self._live_stats_config.enabled
        )

        # Create display (if monitoring enabled)
        if self._live_stats_config.enabled:
            self._display = LiveProgressDisplay(
                scenarios=self._scenarios,
                live_queue=self._live_queue,
                update_interval=self._live_stats_config.update_interval_sec
            )
        else:
            self._display = None

        # Log live stats setup
        if self._live_stats_config.enabled:
            mode = "DETAILED" if self._live_stats_config.detailed_mode else "BASIC"
            self._logger.info(
                f"📊 Live stats: {mode} mode "
                f"(update interval: {self._live_stats_config.update_interval_sec:.2f}s)"
            )
            if self._live_stats_config.detailed_mode:
                self._logger.debug(
                    f"   Exports: Portfolio={self._live_stats_config.export_portfolio_stats}, "
                    f"Performance={self._live_stats_config.export_performance_stats}, "
                    f"Bars={self._live_stats_config.export_current_bars}"
                )

        self._logger.debug(
            f"📦 BatchOrchestrator initialized: "
            f"scenario_set='{self.scenario_set_name}', "
            f"run_timestamp='{self.logger_start_time_format}', "
            f"{len(self._scenarios)} scenario(s)"
        )

    def run(self) -> BatchExecutionSummary:
        """
        Execute all scenarios with coordinated phases.

        WORKFLOW:
        Phase 1: Index & Coverage Setup (Serial)
        Phase 2: Availability Validation (Serial)
        Phase 3: Requirements Collection (Serial)
        Phase 4: Data Loading (Serial)
        Phase 5: Quality Validation (Serial)
        Phase 6: Execution (Parallel/Sequential)
        Phase 7: Summary & Reporting

        Returns:
            BatchExecutionSummary with aggregated results from all scenarios
        """
        scenario_count = len(self._scenarios)
        force_sequential = scenario_count == 1

        if force_sequential:
            self._logger.info(
                "⚠️ Sequential execution forced - only one scenario in set."
            )

        self._logger.info(
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
        # PHASE 0: CONFIG VALIDATION
        # ========================================================================
        self._logger.info("🔍 Phase 0: Validating configuration...")

        # 1. Validate scenario names (unique, non-empty)
        ScenarioValidator.validate_scenario_names(
            scenarios=self._scenario_set.get_valid_scenarios(),
            logger=self._logger
        )

        # 2. Validate scenario boundaries (end_date or max_ticks required)
        ScenarioValidator.validate_scenario_boundaries(
            scenarios=self._scenario_set.get_valid_scenarios(),
            logger=self._logger
        )

        # 3. Validate account_currency compatibility with symbols
        ScenarioValidator.validate_account_currencies(
            scenarios=self._scenario_set.get_valid_scenarios(),
            logger=self._logger
        )

        # set scenario final currencies.
        ScenarioValidator.set_scenario_account_currency(
            scenarios=self._scenario_set.get_valid_scenarios(),
            logger=self._logger
        )

        # ========================================================================
        # PHASE 1: INDEX & COVERAGE SETUP
        # ========================================================================
        self._logger.info("📊 Phase 1: Index & coverage setup...")

        data_coordinator = DataPreparationCoordinator(
            scenarios=self._scenario_set.get_valid_scenarios(),
            logger=self._logger,
            app_config=self._app_config_manager
        )

        # Build tick index and generate coverage reports
        tick_index_manager = data_coordinator.get_tick_index_manager()
        coverage_report_manager = DataCoverageReportManager(
            logger=self._logger,
            scenarios=self._scenario_set.get_valid_scenarios(),
            tick_index_manager=tick_index_manager,
            app_config=self._app_config_manager
        )
        coverage_report_manager.generate_reports()

        # ========================================================================
        # PHASE 2: AVAILABILITY VALIDATION
        # ========================================================================
        self._logger.info("🔍 Phase 2: Validating data availability...")

        # Validate that all scenarios have data available
        # IMPORTANT: Initializes validation_result for ALL SingleScenario objects
        coverage_report_manager.validate_availability(
            scenarios=self._scenario_set.get_valid_scenarios()
        )
        # ========================================================================
        # PHASE 3: REQUIREMENTS COLLECTION
        # ========================================================================
        self._logger.info("📋 Phase 3: Collecting data requirements...")

        # Collect requirements from valid scenarios only
        requirements_map = self._requirements_collector.collect_and_validate(
            self._scenario_set.get_valid_scenarios())

        # ========================================================================
        # PHASE 4: DATA LOADING
        # ========================================================================
        self._logger.info("📦 Phase 4: Loading data...")

        # Prepare data only for scenarios in requirements_map
        scenario_packages, clipping_stats_map = data_coordinator.prepare(
            requirements_map=requirements_map,
            status_broadcaster=self._live_stats_coordinator
        )

        # ========================================================================
        # PHASE 5: QUALITY VALIDATION
        # ========================================================================
        self._logger.info("🔬 Phase 5: Validating data quality...")

        self._live_stats_coordinator.broadcast_status(
            ScenarioStatus.WARMUP_COVERAGE)

        coverage_report_manager.validate_after_load(
            scenarios=self._scenario_set.get_valid_scenarios(),
            scenario_packages=scenario_packages,  # Dict of packages
            requirements_map=requirements_map
        )

        # Calculate total invalid scenarios
        total_invalid = len(self._scenario_set.get_failed_scenarios())
        valid_scenario_count = len(self._scenario_set.get_valid_scenarios())

        self._logger.info(
            f"✅ Continuing with {valid_scenario_count}/{scenario_count} "
            f"invalid scenario(s) ({total_invalid} filtered out)"
        )

        # ========================================================================
        # PHASE 6: EXECUTION
        # ========================================================================
        self._logger.info("🚀 Phase 6: Executing scenarios...")

        batch_warmup_time = time.time() - start_time
        batch_tickrun_start = time.time()

        # Execute scenarios
        if self._parallel_scenarios and scenario_count > 1:
            results = self._execution_coordinator.execute_parallel(
                scenarios=self._scenario_set.get_all_scenarios(),
                scenario_packages=scenario_packages,  # Dict of packages
                live_queue=self._live_queue
            )

        else:
            results = self._execution_coordinator.execute_sequential(
                scenarios=self._scenario_set.get_all_scenarios(),
                scenario_packages=scenario_packages,  # Dict of packages
                live_queue=self._live_queue
            )

        # calc execution time
        batch_tickrun_time = time.time() - batch_tickrun_start
        batch_execution_time = time.time() - start_time

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
        # PHASE 7: SUMMARY & REPORTING
        # ========================================================================
        self._logger.info("📊 Phase 7: Building summary...")

        summary = BatchExecutionSummary(
            # results of scenario
            process_result_list=results,
            # scenarios always stay in index synchronisity with results.
            single_scenario_list=self._scenarios,
            # stats for batch execution
            batch_execution_time=batch_execution_time,
            batch_warmup_time=batch_warmup_time,
            batch_tickrun_time=batch_tickrun_time,
            # broker maps are a set of symbols used in scenario_set
            broker_scenario_map=data_coordinator.get_broker_scenario_map(),
            # clipping stats from tick processing budget (main process, not subprocess)
            clipping_stats_map=clipping_stats_map,
        )

        self._logger.verbose(summary.process_result_list)

        # Error handling
        failed_results = [r for r in results if not r.success]
        if failed_results:
            # After all processing, raise comprehensive error
            execution_error = BatchExecutionError(failed_results)
            self._logger.error(execution_error.get_message())

        self.flush_all_logs(summary)

        self._logger.info(
            f"✅ Batch execution completed in {batch_execution_time:.2f}s"
        )

        return summary

    def flush_all_logs(self, batch_execution_summary: BatchExecutionSummary = None):
        """
        Logger flush. Run does not decide whether to console log.

        Args:
            batch_execution_summary: Summary with scenario results
        """
        # output scenario Logs
        show_scenario_logging = self._app_config_manager.get_logging_show_scenario_logging()
        if show_scenario_logging:
            for process_result in batch_execution_summary.process_result_list:
                AbstractLogger.print_buffer(
                    process_result.scenario_logger_buffer,
                    process_result.scenario_name
                )

        # global log comes last.
        self._logger.close(True)
