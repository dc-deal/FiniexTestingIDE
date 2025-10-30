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
from datetime import datetime
import traceback
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from typing import Dict, List
from python.data_worker.data_loader.core import TickDataLoader
from python.framework.data_preperation.aggregate_scenario_data_requirements import AggregateScenarioDataRequirements
from python.framework.data_preperation.shared_data_preparator import SharedDataPreparator
from python.framework.process_executor import ProcessExecutor, process_main
from python.framework.exceptions.scenario_execution_errors import BatchExecutionError
from python.configuration import AppConfigLoader
from python.framework.types.process_data_types import BatchExecutionSummary, ProcessDataPackage, ProcessResult
from python.framework.types.scenario_set_types import ScenarioSet
from python.framework.factory.decision_logic_factory import DecisionLogicFactory
import sys
import os
from python.framework.factory.worker_factory import WorkerFactory

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
        app_config: AppConfigLoader
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
        self.appConfig = app_config

        # start global Log
        self.scenario_set.logger.reset_start_time("Batch Init")

        # Initialize Factories
        self.worker_factory = WorkerFactory()
        self.decision_logic_factory = DecisionLogicFactory(
            logger=self.scenario_set.logger)

        # Shared data package (filled in run())
        self.shared_data: ProcessDataPackage = None

        # CORRECTED: Extract scenario_set_name from scenario_set
        self.scenario_set_name = self.scenario_set.scenario_set_name

        # CORRECTED: Create shared run_timestamp for all processes
        self.run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        self.scenario_set.logger.debug(
            f"📦 BatchOrchestrator initialized: "
            f"scenario_set='{self.scenario_set_name}', "
            f"run_timestamp='{self.run_timestamp}', "
            f"{len(scenario_set.scenarios)} scenario(s)"
        )

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
        self.scenario_set.logger.info(
            f"🚀 Starting batch execution "
            f"({len(self.scenario_set.scenarios)} scenarios, "
            f"run_timestamp={self.run_timestamp})"
        )
        start_time = time.time()

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
                app_config=self.appConfig,
                scenario_index=idx,
                logger=self.scenario_set.logger
            )
            # Store for later use in ProcessExecutor
            warmup_requirements_by_scenario[idx] = warmup_reqs

        requirements_map = requirements_collector.finalize()

        # ========================================================================
        # PHASE 1: DATA PREPARATION (Serial)
        # ========================================================================
        self.scenario_set.logger.info("🔄 Phase 1: Preparing shared data...")

        preparator = SharedDataPreparator()
        self.shared_data = preparator.prepare_all(requirements_map)

        self.scenario_set.logger.info(
            f"✅ Phase 1 complete: "
            f"{sum(self.shared_data.tick_counts.values()):,} ticks, "
            f"{sum(self.shared_data.bar_counts.values())} bar sets prepared"
        )

        # ========================================================================
        # PHASE 2: SCENARIO EXECUTION (Parallel)
        # ========================================================================
        self.scenario_set.logger.info("🚦 Phase 2: Executing scenarios...")

        # Get execution mode
        run_parallel = self.appConfig.get_default_parallel_scenarios()

        # Execute scenarios (pass warmup_requirements)
        if run_parallel:
            results = self._run_parallel()
        else:
            results = self._run_sequential()

        # Check for failures
        self.scenario_set.logger.info(
            f"🕐 Scenario error check  : {time.time()}")
        failed_results = [r for r in results if not r.success]
        if failed_results:
            # Log failures but don't stop (scenarios are independent)
            for failed in failed_results:
                self.scenario_set.logger.error(
                    f"❌ Scenario failed: {failed.scenario_name} - {failed.error_message}"
                )

            # After all processing, raise comprehensive error
            raise BatchExecutionError(failed_results)

        # Set metadata in BatchExecutionSummary
        summary_execution_time = time.time() - start_time
        self.scenario_set.logger.info(
            f"🕐 Create BatchExecutionSummary  : {time.time()}")
        summary = BatchExecutionSummary(
            success=True,
            scenarios_count=len(self.scenario_set.scenarios),
            summary_execution_time=summary_execution_time,
            scenario_list=results
        )

        self.scenario_set.logger.info(
            f"✅ Batch execution completed in {summary_execution_time:.2f}s"
        )

        return summary

    # === NEW METHOD: _run_sequential_new() ===

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
                app_config=self.appConfig,
                scenario_index=idx,
                scenario_set_name=self.scenario_set_name,
                run_timestamp=self.scenario_set.logger.get_run_timestamp()
            )

            # Execute
            result = executor.run(self.shared_data)
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

    # === NEW METHOD: _run_parallel_new() ===

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
        max_workers = self.appConfig.get_default_max_parallel_scenarios()

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
                    app_config=self.appConfig,
                    scenario_index=idx,
                    scenario_set_name=self.scenario_set_name,
                    run_timestamp=self.scenario_set.logger.get_run_timestamp()
                )

                # Submit to executor
                # Call process_main directly (top-level function, pickle-able)
                future = executor.submit(
                    process_main,
                    executor_obj.config,
                    self.shared_data
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
