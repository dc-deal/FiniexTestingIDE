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
from python.framework.process.process_executor import ProcessExecutor
from multiprocessing import Manager
import os
from python.components.display.live_progress_display import LiveProgressDisplay
from python.framework.data_preperation.broker_data_preperator import BrokerDataPreparator
from python.framework.factory.worker_factory import WorkerFactory
import sys
from python.framework.factory.decision_logic_factory import DecisionLogicFactory
from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.types.live_scenario_stats_types import LiveScenarioStats
from python.framework.types.live_stats_config_types import LiveStatsExportConfig, ScenarioStatus
from python.framework.types.scenario_set_types import ScenarioSet
from python.framework.types.process_data_types import ProcessDataPackage, ProcessResult
from python.configuration import AppConfigLoader
from python.framework.exceptions.scenario_execution_errors import BatchExecutionError
from python.framework.process.process_main import process_main
from python.framework.data_preperation.shared_data_preparator import SharedDataPreparator
from python.framework.data_preperation.aggregate_scenario_data_requirements import AggregateScenarioDataRequirements
from python.data_worker.data_loader.data_loader_core import TickDataLoader
from python.components.logger.abstract_logger import AbstractLogger
from typing import Dict, List
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
import time
import traceback

# Auto-detect if debugger is attached
DEBUGGER_ACTIVE = (
    hasattr(sys, 'gettrace') and sys.gettrace() is not None
    or 'debugpy' in sys.modules
    or 'pydevd' in sys.modules
)


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
        data_worker: TickDataLoader,
        app_config_loader: AppConfigLoader
    ):
        """
        Initialize batch orchestrator.

        CORRECTED: Creates run_timestamp for shared logger initialization.

        Args:
            scenario_set: Set of scenarios to execute
            data_worker: TickDataLoader instance (kept for backwards compatibility)
            app_config: Application configuration
        """
        self.scenario_set = scenario_set
        self.data_worker = data_worker  # Kept but not used in new flow
        self._app_config_loader = app_config_loader
        self._parallel_scenarios = app_config_loader.get_default_parallel_scenarios()
        self._preparator: SharedDataPreparator = None

        # start global Log
        self.scenario_set.logger.reset_start_time()
        self.scenario_set.logger.info("🚀 Starting Scenario " +
                                      self.scenario_set.scenario_set_name+" Log Timer (Batch Init).")

        # Initialize Factories
        self.worker_factory = WorkerFactory(logger=self.scenario_set.logger)
        self.decision_logic_factory = DecisionLogicFactory(
            logger=self.scenario_set.logger)

        # Shared data package (filled in run())
        self.shared_data: ProcessDataPackage = None

        # CORRECTED: Extract scenario_set_name from scenario_set
        self.scenario_set_name = self.scenario_set.scenario_set_name

        # CORRECTED: Create shared run_timestamp for all processes
        self.logger_start_time_format = self.scenario_set.logger.get_run_timestamp()

        # Live stats config
        self.live_stats_config = LiveStatsExportConfig.from_app_config(
            self._app_config_loader.get_config(),
            len(scenario_set.scenarios)
        )

        # Create queue (if monitoring enabled)
        if self.live_stats_config.enabled:
            self.manager = Manager()  # ← Store manager reference!
            self.live_queue = self.manager.Queue(maxsize=100)
        else:
            self.manager = None
            self.live_queue = None

        # Initialize live stats cache
        self._live_stats_cache: Dict[int, LiveScenarioStats] = {}
        self._init_live_stats()

        # Create display (if monitoring enabled)
        if self.live_stats_config.enabled:
            self.display = LiveProgressDisplay(
                scenarios=scenario_set.scenarios,
                live_queue=self.live_queue,
                update_interval=self.live_stats_config.update_interval_sec
            )
        else:
            self.display = None

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

    def _init_live_stats(self) -> None:
        """Initialize LiveScenarioStats cache for all scenarios."""
        if not self.live_stats_config.enabled:
            return

        for idx, scenario in enumerate(self.scenario_set.scenarios):
            self._live_stats_cache[idx] = LiveScenarioStats(
                scenario_name=scenario.name,
                symbol=scenario.symbol,
                scenario_index=idx,
                status=ScenarioStatus.INITIALIZED
            )

    def _prepare_shared_data(
        self
    ) -> None:
        """
        Phase 1: Prepare shared data with status updates.

        Args:
            requirements_collector: Requirements collector with finalized map
        """
        # ========================================================================
        # PHASE 0: REQUIREMENTS COLLECTION (Serial)
        # ========================================================================
        self.scenario_set.logger.info(
            "📋 Phase 0: Collecting data requirements...")

        requirements_collector = AggregateScenarioDataRequirements()

        # CORRECTED: Store warmup requirements per scenario
        warmup_requirements_by_scenario = {}

        for idx, scenario in enumerate(self.scenario_set.scenarios):
            warmup_reqs = requirements_collector.add_scenario(
                scenario=scenario,
                app_config=self._app_config_loader,
                scenario_index=idx,
                logger=self.scenario_set.logger
            )
            # Store for later use in ProcessExecutor
            warmup_requirements_by_scenario[idx] = warmup_reqs
        requirements_map = requirements_collector.finalize()

        # ========================================================================
        # DATA PREPARATION (Serial)
        # ========================================================================
        self.scenario_set.logger.info("🔄 Phase 1: Preparing shared data...")

        # ========================================================================
        # 1.1 PREPARE BARS & TICKS
        # ========================================================================
        self._data_preparator = SharedDataPreparator(self.scenario_set.logger)

        # === STATUS: WARMUP_DATA_TICKS ===
        self._broadcast_status(ScenarioStatus.WARMUP_DATA_TICKS)

        # === PHASE 1A: Load Ticks ===
        ticks_data, tick_counts, tick_ranges = self._data_preparator.prepare_ticks(
            requirements_map.tick_requirements
        )

        # === STATUS: WARMUP_DATA_BARS ===
        self._broadcast_status(ScenarioStatus.WARMUP_DATA_BARS)

        # === PHASE 1B: Load Bars ===
        bars_data, bar_counts = self._data_preparator.prepare_bars(
            requirements_map.bar_requirements
        )

        # === PHASE 1C: Package Data ===
        self.shared_data = ProcessDataPackage(
            ticks=ticks_data,
            bars=bars_data,
            tick_counts=tick_counts,
            tick_ranges=tick_ranges,
            bar_counts=bar_counts,
            broker_configs=None
        )

        # Log summary
        total_ticks = sum(tick_counts.values())
        total_bars = sum(bar_counts.values())

        self.scenario_set.logger.info(
            f"✅ Data prepared: {total_ticks:,} ticks, {total_bars:,} bars "
            f"({len(ticks_data)} tick sets, {len(bars_data)} bar sets)"
        )

        # === STATUS: WARMUP_TRADER ===
        self._broadcast_status(ScenarioStatus.WARMUP_TRADER)

        # ========================================================================
        # 1.2 PREPARE BROKER CONFIG
        # ========================================================================
        self._broker_preparator = BrokerDataPreparator(
            self.scenario_set.scenarios,
            self.scenario_set.logger
        )
        self.shared_data.broker_configs = self._broker_preparator.prepare()

    def _broadcast_status(self, status: ScenarioStatus) -> None:
        """
        Broadcast status update for all scenarios.

        Args:
            status: New status for all scenarios
        """
        if not self.live_stats_config.enabled:
            return

        for idx, stats in self._live_stats_cache.items():
            stats.status = status

            try:
                self.live_queue.put_nowait({
                    "type": "status",
                    "scenario_index": idx,
                    "scenario_name": stats.scenario_name,
                    "status": status.value
                })
            except:
                pass  # Queue full - skip update

    # === REPLACE run() METHOD ===

    def run(self) -> BatchExecutionSummary:
        """
        Execute all scenarios with new ProcessPool-ready architecture.

        WORKFLOW:
        Phase 0: Requirements Collection (Serial)
        Phase 1: Data Preparation (Serial)
        Phase 2: Scenario Execution (Parallel - ThreadPool or ProcessPool)

        CORRECTED:
        - Uses run_timestamp and scenario_set_name throughout
        - Properly passes warmup requirements to ProcessExecutor

        Returns:
            Aggregated results from all scenarios
        """
        scenario_count = len(self.scenario_set.scenarios)
        force_parallel = scenario_count == 1

        if (force_parallel):
            self.scenario_set.logger.info(
                f"⚠️ Sequential execution forced - only one scenario in set.")
        self.scenario_set.logger.info(
            f"🚀 Starting batch execution "
            f"({scenario_count} scenarios, "
            f"run_timestamp={self.logger_start_time_format})")
        start_time = time.time()
        self._broadcast_status(ScenarioStatus.INITIALIZED)

        # ... Phase 2: Scenario Execution ...
        # === ADD: Start/Stop Display ===
        # Location: Before executing scenarios

        # Start live display
        if self.display:
            self.display.start()

        # prepare all data
        self._prepare_shared_data()

        # ========================================================================
        # PHASE 2: SCENARIO EXECUTION (Parallel)
        # ========================================================================
        self.scenario_set.logger.info("🚦 Phase 2: Executing scenarios...")

        # Get execution mode

        # Execute scenarios (pass warmup_requirements)
        if self._parallel_scenarios and scenario_count > 1:
            results = self._run_parallel()
        else:
            results = self._run_sequential()

        # Set metadata in BatchExecutionSummary
        summary_execution_time = time.time() - start_time

        # ========================================================================
        # Cleanup display
        # ========================================================================
        # === ADD: Cleanup in finally block ===
        if self.display and self.display._running:
            self.display.stop()
        if self.manager:
            try:
                self.manager.shutdown()
            except:
                pass

        # ========================================================================
        # PHASE 3: Return Values & Summary
        # ========================================================================
        self.scenario_set.logger.info(
            f"🕐 Create BatchExecutionSummary  : {time.time()}")

        # build result and flush logs
        summary = BatchExecutionSummary(
            success=True,
            scenarios_count=len(self.scenario_set.scenarios),
            summary_execution_time=summary_execution_time,
            broker_scenario_map=self._broker_preparator.get_broker_scenario_map(),
            scenario_list=results
        )
        self.flush_all_logs(summary)

        # Error handling.
        self.scenario_set.logger.info(
            f"🕐 Scenario error check  : {time.time()}")
        failed_results = [r for r in results if not r.success]
        if failed_results:
            # Log failures but don't stop (scenarios are independent)
            for failed in failed_results:
                self.scenario_set.logger.error(
                    f"❌ Scenario failed - flushing Log: {failed.scenario_name} - {failed.error_message}"
                )
                self.scenario_set.logger.flush_buffer()

            # After all processing, raise comprehensive error
            raise BatchExecutionError(failed_results)

        self.scenario_set.logger.info(
            f"✅ Batch execution completed in {summary_execution_time:.2f}s"
        )

        return summary

    def _run_sequential(
        self
    ) -> List[ProcessResult]:
        """
        Execute scenarios sequentially with new architecture.

        CORRECTED: Passes scenario_set_name and run_timestamp to ProcessExecutor.

        Args:
            warmup_requirements_by_scenario: {scenario_idx: {timeframe: warmup_count}}

        Returns:
            List of ProcessResult objects
        """
        results = []

        for idx, scenario in enumerate(self.scenario_set.scenarios):
            readable_index = idx + 1
            self.scenario_set.logger.info(
                f"▶️  Executing scenario {readable_index}/{len(self.scenario_set.scenarios)}: "
                f"{scenario.name}"
            )

            # Create executor with corrected parameters
            executor = ProcessExecutor(
                scenario=scenario,
                app_config_loader=self._app_config_loader,
                scenario_index=idx,
                scenario_set_name=self.scenario_set_name,
                run_timestamp=self.scenario_set.logger.get_run_timestamp(),
                live_stats_config=self.live_stats_config
            )
            # Execute
            result = executor.run(self.shared_data, self.live_queue)
            results.append(result)

            if result.success:
                self.scenario_set.logger.info(
                    f"✅ Scenario {readable_index} completed in "
                    f"{result.execution_time_ms:.0f}ms"
                )
            else:
                self.scenario_set.logger.error(
                    f"❌ Scenario {readable_index} failed: {result.error_message}"
                )

        return results

    def _run_parallel(
        self,
    ) -> List[ProcessResult]:
        """
        Execute scenarios in parallel with new architecture.

        QUICK SWITCH: ThreadPoolExecutor vs ProcessPoolExecutor
        Change USE_PROCESSPOOL to switch between threading and multiprocessing.

        CORRECTED: Passes scenario_set_name and run_timestamp to ProcessExecutor.

        Args:
            warmup_requirements_by_scenario: {scenario_idx: {timeframe: warmup_count}}

        Returns:
            List of ProcessResult objects
        """
        # Auto-switch based on environment
        if DEBUGGER_ACTIVE or os.getenv('DEBUG_MODE'):
            USE_PROCESSPOOL = False
            self.scenario_set.logger.warning(
                "⚠️  Debugger detected - using ThreadPool "
                "(performance not representative!)"
            )
        else:
            USE_PROCESSPOOL = True
            self.scenario_set.logger.info(
                "🚀 Performance mode - using ProcessPool"
            )

        executor_class = ProcessPoolExecutor if USE_PROCESSPOOL else ThreadPoolExecutor
        max_workers = self._app_config_loader.get_default_max_parallel_scenarios()

        self.scenario_set.logger.info(
            f"🔀 Parallel execution: {executor_class.__name__} "
            f"(max_workers={max_workers})"
        )

        results = [None] * len(self.scenario_set.scenarios)

        with executor_class(max_workers=max_workers) as executor:
            # Submit all scenarios
            futures = {}
            for idx, scenario in enumerate(self.scenario_set.scenarios):
                # Create executor with corrected parameters
                executor_obj = ProcessExecutor(
                    scenario=scenario,
                    app_config_loader=self._app_config_loader,
                    scenario_index=idx,
                    scenario_set_name=self.scenario_set_name,
                    run_timestamp=self.scenario_set.logger.get_run_timestamp(),
                    live_stats_config=self.live_stats_config
                )

                # Submit to executor
                # Call process_main directly (top-level function, pickle-able)
                future = executor.submit(
                    process_main,
                    executor_obj.config,
                    self.shared_data,
                    self.live_queue  # ← ADD queue!
                )
                futures[future] = idx

            # Collect results
            from concurrent.futures import as_completed
            for future in as_completed(futures):
                idx = futures[future]
                readable_index = idx + 1

                try:
                    result = future.result()
                    results[idx] = result

                    if result.success:
                        self.scenario_set.logger.info(
                            f"✅ Scenario {readable_index} completed: "
                            f"{result.scenario_name} ({result.execution_time_ms:.0f}ms)"
                        )
                    else:
                        self.scenario_set.logger.error(
                            f"❌ Scenario {readable_index} failed: "
                            f"{result.scenario_name} - {result.error_message}"
                        )

                except Exception as e:
                    # Unexpected error (not caught in process_main)
                    self.scenario_set.logger.error(
                        f"❌ Scenario {readable_index} crashed: "
                        f"\n{traceback.format_exc()}"
                    )
                    results[idx] = ProcessResult(
                        success=False,
                        scenario_name=self.scenario_set.scenarios[idx].name,
                        symbol=self.scenario_set.scenarios[idx].symbol,
                        scenario_index=idx,
                        error_type=type(e).__name__,
                        error_message=str(e),
                        traceback=traceback.format_exc()
                    )
            self.scenario_set.logger.info(
                "🕐 All futures collected, exiting context manager...")
            self.scenario_set.logger.info(
                "🕐 If a major slowdown occurs here, " +
                "it's just the debugger who waits for processes." +
                " You can't skip this...")

        self.scenario_set.logger.info(
            f"🕐 ProcessPoolExecutor shutdown complete! Time: {time.time()}")
        return results

    def flush_all_logs(self, batch_execution_summary: BatchExecutionSummary = None):
        """ 
        Logger Flush. Run does not decide weather to Console log Run.
        """
        self.scenario_set.logger.close(True)
        show_scenario_logging = self._app_config_loader.get_logging_show_scenario_logging()
        if show_scenario_logging:
            for process_result in batch_execution_summary.scenario_list:
                AbstractLogger.print_buffer(
                    process_result.scenario_logger_buffer, process_result.scenario_name)
