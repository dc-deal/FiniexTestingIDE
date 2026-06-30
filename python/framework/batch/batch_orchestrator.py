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
from typing import Any, Dict, List, Optional, Tuple
from python.framework.validators.scenario_validator import ScenarioValidator
from python.framework.validators.post_run_validator import PostRunValidator
from multiprocessing import Manager
from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.exceptions.scenario_execution_errors import BatchExecutionError
from python.configuration.app_config_manager import AppConfigManager
from python.framework.types.scenario_types.scenario_set_types import ScenarioSet, SingleScenario
from python.framework.types.live_types.live_stats_config_types import LiveStatsExportConfig, ScenarioStatus
from python.framework.types.batch_execution_types import BatchExecutionSummary, WarmupPhaseEntry
from python.framework.factory.decision_logic_factory import DecisionLogicFactory
from python.system.ui.live_progress_display import LiveProgressDisplay
from python.framework.batch.live_stats_coordinator import LiveStatsCoordinator
from python.framework.batch.execution_coordinator import ExecutionCoordinator
from python.framework.batch.requirements_collector import RequirementsCollector
from python.framework.utils.runtime_env_utils import is_debug_execution
from python.framework.discoveries.data_coverage.data_coverage_report_manager import DataCoverageReportManager
from python.framework.batch.data_preparation_coordinator import DataPreparationCoordinator
from python.framework.data_preparation.broker_data_preparator import BrokerDataPreparator
from python.framework.types.mount_package_types import DataIdentityKey, MountPackage
from python.framework.types.trading_env_types.broker_types import BrokerType
from python.framework.exceptions.mount_errors import MountIdentityMismatchError


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
            logger=self._logger,
            run_group=self._scenario_set.run_group
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
                update_interval=self._live_stats_config.update_interval_ms / 1000.0
            )
        else:
            self._display = None

        # Log live stats setup
        if self._live_stats_config.enabled:
            mode = "DETAILED" if self._live_stats_config.detailed_mode else "BASIC"
            self._logger.info(
                f"📊 Live stats: {mode} mode "
                f"(update interval: {self._live_stats_config.update_interval_ms:.0f}ms)"
            )
            if self._live_stats_config.detailed_mode:
                self._logger.debug(
                    f"   Exports: Portfolio={self._live_stats_config.export_portfolio_stats}, "
                    f"Bars={self._live_stats_config.export_current_bars}"
                )

        self._logger.debug(
            f"📦 BatchOrchestrator initialized: "
            f"scenario_set='{self.scenario_set_name}', "
            f"run_timestamp='{self.logger_start_time_format}', "
            f"{len(self._scenarios)} scenario(s)"
        )

    def run(self, mount: Optional[MountPackage] = None) -> BatchExecutionSummary:
        """
        Execute all scenarios: validate parameters (fail-fast) → prepare the data → execute.

        The body is split into prepare_mount() (the data-identity-dependent half: validation +
        load) and execute() (the strategy-dependent half: run + summary). Cold path (mount=None)
        builds its own mount. A sweep (#419) passes a shared mount built once from the base set:
        the scenarios are prepped and executed against it WITHOUT reloading, as long as the data
        identity matches; a warmup-affecting swept parameter (identity mismatch) falls back to a
        cold reload for this combination.

        Args:
            mount: Optional shared data mount to reuse (#419); None → build a fresh one (cold)

        Returns:
            BatchExecutionSummary with aggregated results from all scenarios
        """
        self._logger.info(
            f"🚀 Starting batch execution "
            f"({len(self._scenarios)} scenarios, "
            f"run_timestamp={self.logger_start_time_format})"
        )

        self._live_stats_coordinator.broadcast_status(
            ScenarioStatus.INITIALIZED)

        # Start live display (cold path owns the display lifecycle; #419 manages warm reuse).
        if self._display:
            self._display.start()

        # Parameter validation runs BEFORE the mount (fail-fast): an invalid parameter must
        # never trigger the expensive data load. It is the only check that re-runs per run, so
        # it stays a standalone step — prepare_mount stays data-only and execute() stays pure.
        ScenarioValidator.validate_scenario_parameters(
            scenarios=self._scenario_set.get_valid_scenarios(),
            logger=self._logger,
        )

        if mount is not None:
            # Warm path (#419): prep the scenarios (cheap), then reuse the shared mount's data
            # if this combination's data identity matches it; otherwise reload for this combo.
            self.prepare_scenarios()
            if not self.matches_mount(mount):
                self._logger.warning(
                    "⚠️ Combination data identity differs from the mount "
                    "(warmup-affecting parameter) — reloading data for this combination")
                mount = self.prepare_mount()
        else:
            mount = self.prepare_mount()

        summary = self.execute(
            mount, self._scenario_set.get_all_scenarios())

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

        self.flush_all_logs(summary)

        return summary

    def prepare_scenarios(self) -> Tuple[Dict[BrokerType, Dict[str, Any]], BrokerDataPreparator, WarmupPhaseEntry]:
        """
        Phase 0 — data-identity validation: load broker configs (which set scenario.broker_type and
        the account currency) and run the five data-identity validators.

        This is the cheap per-run scenario preparation, separate from the expensive data load — so a
        sweep can prepare a combination's scenarios and reuse a mount without reloading (#419).
        prepare_mount runs it first; the sweep runner calls it directly per combination.

        Returns:
            (broker_configs, broker_preparator, the 'Config Validation' warmup-phase timing)
        """
        self._logger.info("🔍 Phase 0: Validating configuration...")
        _phase_t = time.time()

        # Load broker configs first — sets scenario.broker_type, needed by validators.
        self._live_stats_coordinator.broadcast_status(ScenarioStatus.WARMUP_TRADER)
        _broker_preparator = BrokerDataPreparator(
            self._scenario_set.get_valid_scenarios(), self._logger)
        _broker_configs = _broker_preparator.prepare()
        _broker_scenario_map = _broker_preparator.get_broker_scenario_map()

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

        # 3. Validate each symbol is registered in its broker config
        ScenarioValidator.validate_scenario_symbols(
            scenarios=self._scenario_set.get_valid_scenarios(),
            logger=self._logger,
            broker_scenario_map=_broker_scenario_map,
        )

        # 3b. Validate each symbol's swap_mode is modeled by the swap engine (#407)
        ScenarioValidator.validate_swap_modes(
            scenarios=self._scenario_set.get_valid_scenarios(),
            logger=self._logger,
            broker_scenario_map=_broker_scenario_map,
        )

        # 4. Validate account_currency compatibility with symbols
        ScenarioValidator.validate_account_currencies(
            scenarios=self._scenario_set.get_valid_scenarios(),
            logger=self._logger,
            broker_scenario_map=_broker_scenario_map,
        )

        # set scenario final currencies.
        ScenarioValidator.set_scenario_account_currency(
            scenarios=self._scenario_set.get_valid_scenarios(),
            logger=self._logger,
            broker_scenario_map=_broker_scenario_map,
        )

        return _broker_configs, _broker_preparator, WarmupPhaseEntry(
            'Config Validation', time.time() - _phase_t)

    def prepare_mount(self) -> MountPackage:
        """
        Prepare the reusable data mount: data-identity validation + data load + packaging.

        The data-identity-dependent half of a batch (Phase 0 data validators + Phases 1–5).
        Produces a self-contained MountPackage keyed by the data identity, so it can be held
        resident (#418) and fed a new parameter set via execute(mount, scenarios) (#419)
        without reloading. Parameter validation is intentionally NOT here — it is the per-run
        check owned by the caller (run() / the sweep runner).

        Returns:
            MountPackage with the loaded per-scenario data and the data identity that keys it
        """
        start_time = time.time()
        warmup_phases = []

        # Phase 0 — data-identity validation (broker prep + validators), extracted so the sweep
        # runner can prep a combination's scenarios without reloading (#419).
        _broker_configs, _broker_preparator, _config_validation_phase = self.prepare_scenarios()
        warmup_phases.append(_config_validation_phase)

        # ========================================================================
        # PHASE 1: INDEX & COVERAGE SETUP
        # ========================================================================
        self._logger.info("📊 Phase 1: Index & coverage setup...")
        _phase_t = time.time()

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
            app_config=self._app_config_manager,
        )
        coverage_report_manager.generate_reports()
        warmup_phases.append(WarmupPhaseEntry('Index & Coverage', time.time() - _phase_t))

        # ========================================================================
        # PHASE 2: AVAILABILITY VALIDATION
        # ========================================================================
        self._logger.info("🔍 Phase 2: Validating data availability...")
        _phase_t = time.time()

        # Validate that all scenarios have data available
        # IMPORTANT: Initializes validation_result for ALL SingleScenario objects
        coverage_report_manager.validate_availability(
            scenarios=self._scenario_set.get_valid_scenarios()
        )
        warmup_phases.append(WarmupPhaseEntry('Availability Check', time.time() - _phase_t))

        # ========================================================================
        # PHASE 3: REQUIREMENTS COLLECTION
        # ========================================================================
        self._logger.info("📋 Phase 3: Collecting data requirements...")
        _phase_t = time.time()

        # Collect requirements from valid scenarios only
        requirements_map = self._requirements_collector.collect_and_validate(
            self._scenario_set.get_valid_scenarios())
        warmup_phases.append(WarmupPhaseEntry('Requirements', time.time() - _phase_t))

        # ========================================================================
        # PHASE 4: DATA LOADING
        # ========================================================================
        self._logger.info("📦 Phase 4: Loading data...")

        # Prepare data only for scenarios in requirements_map
        scenario_packages, clipping_stats_map, load_timings = data_coordinator.prepare(
            requirements_map=requirements_map,
            broker_configs=_broker_configs,
            status_broadcaster=self._live_stats_coordinator
        )
        warmup_phases.append(WarmupPhaseEntry('Data Loading → Ticks (parquet)', load_timings.ticks_s))
        warmup_phases.append(WarmupPhaseEntry('Data Loading → Bars (parquet)', load_timings.bars_s))
        warmup_phases.append(WarmupPhaseEntry('Data Loading → Packaging', load_timings.packaging_s))

        # ========================================================================
        # PHASE 5: QUALITY VALIDATION
        # ========================================================================
        self._logger.info("🔬 Phase 5: Validating data quality...")
        _phase_t = time.time()

        self._live_stats_coordinator.broadcast_status(
            ScenarioStatus.WARMUP_COVERAGE)

        coverage_report_manager.validate_after_load(
            scenarios=self._scenario_set.get_valid_scenarios(),
            scenario_packages=scenario_packages,  # Dict of packages
            requirements_map=requirements_map
        )
        warmup_phases.append(WarmupPhaseEntry('Quality Validation', time.time() - _phase_t))

        # Calculate total invalid scenarios
        scenario_count = len(self._scenarios)
        total_invalid = len(self._scenario_set.get_failed_scenarios())
        valid_scenario_count = len(self._scenario_set.get_valid_scenarios())

        self._logger.info(
            f"✅ Continuing with {valid_scenario_count}/{scenario_count} "
            f"invalid scenario(s) ({total_invalid} filtered out)"
        )

        # Data identity — fingerprint each loaded scenario's data (broker / symbol / window /
        # warmup / tick budget, NOT strategy_config). The key #418/#419 reuse a mount on and the
        # execute() guard checks each fed scenario against.
        data_identity = {}
        for scenario in self._scenario_set.get_valid_scenarios():
            if scenario.scenario_index in scenario_packages:
                data_identity[scenario.scenario_index] = DataIdentityKey.from_scenario(
                    scenario, requirements_map.bar_requirements)

        batch_warmup_time = time.time() - start_time

        return MountPackage(
            scenario_packages=scenario_packages,
            clipping_stats_map=clipping_stats_map,
            broker_configs=_broker_configs,
            broker_scenario_map=_broker_preparator.get_valid_broker_scenario_map(
                self._scenario_set.get_valid_scenarios()
            ),
            requirements_map=requirements_map,
            warmup_phases=warmup_phases,
            batch_warmup_time=batch_warmup_time,
            data_identity=data_identity,
        )

    def execute(
        self,
        mount: MountPackage,
        scenarios: List[SingleScenario]
    ) -> BatchExecutionSummary:
        """
        Execute scenarios against a prepared mount and build the run summary.

        The strategy-dependent half (Phase 6 + 7). Consumes a MountPackage (the loaded data)
        plus the per-run scenarios (the parameter package). No parameter validation here, so
        repeated execute() over one mount is byte-identical (#368). An identity guard rejects a
        scenarios set whose data identity does not match the mount — the #419/#418 safety
        contract (in the cold path the scenarios built the mount, so it never fires there).

        Args:
            mount: The prepared data mount (from prepare_mount)
            scenarios: The per-run scenarios to execute (carry the parameter set)

        Returns:
            BatchExecutionSummary with aggregated results from all scenarios
        """
        self._assert_mount_identity(mount, scenarios)

        scenario_count = len(scenarios)
        if scenario_count == 1:
            self._logger.info(
                "⚠️ Sequential execution forced - only one scenario in set."
            )

        # ========================================================================
        # PHASE 6: EXECUTION
        # ========================================================================
        self._logger.info("🚀 Phase 6: Executing scenarios...")

        batch_tickrun_start = time.time()

        # Execute scenarios
        if self._parallel_scenarios and scenario_count > 1:
            results, batch_pickle_time, batch_pickle_sample_mb = self._execution_coordinator.execute_parallel(
                scenarios=scenarios,
                scenario_packages=mount.scenario_packages,  # Dict of packages
                live_queue=self._live_queue
            )

        else:
            results, batch_pickle_time, batch_pickle_sample_mb = self._execution_coordinator.execute_sequential(
                scenarios=scenarios,
                scenario_packages=mount.scenario_packages,  # Dict of packages
                live_queue=self._live_queue
            )

        # calc execution time
        batch_tickrun_time = time.time() - batch_tickrun_start
        batch_execution_time = mount.batch_warmup_time + batch_tickrun_time

        # ========================================================================
        # PHASE 7: SUMMARY & REPORTING
        # ========================================================================
        self._logger.info("📊 Phase 7: Building summary...")

        summary = BatchExecutionSummary(
            # results of scenario
            process_result_list=results,
            # scenarios always stay in index synchronisity with results.
            single_scenario_list=scenarios,
            # stats for batch execution
            batch_execution_time=batch_execution_time,
            batch_warmup_time=mount.batch_warmup_time,
            batch_tickrun_time=batch_tickrun_time,
            # broker maps are a set of symbols used in scenario_set
            broker_scenario_map=mount.broker_scenario_map,
            # clipping stats from tick processing budget (main process, not subprocess)
            clipping_stats_map=mount.clipping_stats_map,
            # per-phase warmup timing breakdown
            warmup_phases=mount.warmup_phases,
            # main-process serialization time (submit loop) + sample size
            batch_pickle_time=batch_pickle_time,
            batch_pickle_sample_mb=batch_pickle_sample_mb,
            # debugger attached / DEBUG_MODE → serial run, timings not representative
            debug_execution=is_debug_execution(),
            # set-wide robustness mode (#367) — read by robustness builder + PostRunValidator
            robustness_config=self._scenario_set.get_robustness_config(),
        )

        self._logger.verbose(summary.process_result_list)

        # Post-run advisory warnings (Tier 1) — debug-mode / stress / data-version / budget.
        # Lifted out of the report renderer into a validator: the structured truth lands on
        # the validation channels, the report only reads it (#395, no decisions in reports).
        PostRunValidator(summary).validate()

        # Error handling — the structured failed-scenario detail is now the warnings/errors
        # model (rendered in the summary + warnings_errors.json + API). The global log keeps
        # only a thin one-line trail (§36).
        failed_results = [r for r in results if not r.success]
        if failed_results:
            self._logger.error(BatchExecutionError(failed_results).get_failure_summary())

        self._logger.info(
            f"✅ Batch execution completed in {batch_execution_time:.2f}s"
        )

        return summary

    def _assert_mount_identity(
        self,
        mount: MountPackage,
        scenarios: List[SingleScenario]
    ) -> None:
        """
        Guard: each executed scenario's data identity must match the mount it runs against.

        The mount holds data for a specific (broker / symbol / window / warmup / budget)
        identity; feeding it scenarios with a different identity would run the wrong data.
        In the cold path the scenarios built the mount → always matches; the guard backstops
        the #419/#418 reuse path.

        Args:
            mount: The prepared data mount
            scenarios: The scenarios about to execute against it
        """
        for scenario in scenarios:
            index = scenario.scenario_index
            if index not in mount.scenario_packages:
                continue  # invalid / no data — not part of the mount

            expected = mount.data_identity.get(index)
            actual = DataIdentityKey.from_scenario(
                scenario, mount.requirements_map.bar_requirements)
            if expected != actual:
                raise MountIdentityMismatchError(
                    f"Scenario '{scenario.name}' (index {index}) data identity does not "
                    f"match the mount (expected {expected}, got {actual})"
                )

    def build_mount(self) -> MountPackage:
        """
        Build a reusable data mount from this set without executing it (the #419 sweep base).

        Validates parameters (fail-fast) then prepares the mount. The sweep runner calls this once
        on the base set to load the data a single time and reuse it across all combinations.

        Returns:
            The prepared MountPackage (empty scenario_packages = a data-level failure → caller aborts)
        """
        ScenarioValidator.validate_scenario_parameters(
            scenarios=self._scenario_set.get_valid_scenarios(),
            logger=self._logger,
        )
        return self.prepare_mount()

    def matches_mount(self, mount: MountPackage) -> bool:
        """
        Whether this set's scenarios share the mount's data identity (so its data is reusable).

        Collects the requirements for the (already-prepped) scenarios and compares each valid
        scenario's DataIdentityKey to the mount's. A warmup-affecting swept parameter changes the
        identity → the mount cannot be reused for this combination (#419 falls back to a reload).

        Args:
            mount: The shared data mount

        Returns:
            True if every valid scenario's data identity matches the mount
        """
        requirements = self._requirements_collector.collect_and_validate(
            self._scenario_set.get_valid_scenarios())
        for scenario in self._scenario_set.get_valid_scenarios():
            index = scenario.scenario_index
            if index not in mount.data_identity:
                return False
            if DataIdentityKey.from_scenario(
                    scenario, requirements.bar_requirements) != mount.data_identity[index]:
                return False
        return True

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
        show_global_log = self._app_config_manager.get_console_logging_config_object().show_global_log
        self._logger.close(flush_buffer=show_global_log)
