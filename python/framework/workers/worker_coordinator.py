"""
FiniexTestingIDE - Worker Coordinator (REFACTORED)
Coordinates multiple workers and delegates decision-making to DecisionLogic

ARCHITECTURE CHANGE (Issue 2):
- Workers are now injected (created by Factory)
- DecisionLogic is now injected (no hardcoded strategy)
- Coordinator only coordinates, doesn't decide

ARCHITECTURE CHANGE (Performance Logging V0.7):
- Integrated PerformanceLogCoordinator for comprehensive metrics
- Automatic performance tracking for workers and decision logic
- No changes needed in concrete worker/logic classes
"""

import traceback
from python.components.logger.bootstrap_logger import setup_logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List

from python.framework.decision_logic.abstract_decision_logic import \
    AbstractDecisionLogic
from python.framework.types import Bar, Decision, TickData, WorkerState
from python.framework.workers.abstract_blackbox_worker import \
    AbstractBlackboxWorker
from python.framework.performance.performance_log_coordinator import \
    PerformanceLogCoordinator

vLog = setup_logging(name="StrategyRunner")


class WorkerCoordinator:
    """
    Orchestrates multiple workers and delegates decision-making to DecisionLogic.

    This class is the central coordination point for tick-by-tick processing.
    It manages worker execution (sequential or parallel) and collects their
    results, then passes those results to the DecisionLogic for decision-making.

    The Coordinator has NO knowledge of specific indicators (RSI, Envelope, etc.)
    or trading strategies - that's all delegated to Workers and DecisionLogic.
    """

    def __init__(
        self,
        workers: List[AbstractBlackboxWorker],
        decision_logic: AbstractDecisionLogic,
        strategy_config: Dict[str, Any],
        parallel_workers: bool = None,
        parallel_threshold_ms: float = 1.0,
        scenario_name: str = "unknown_scenario",
    ):
        """
        Initialize coordinator with injected workers and decision logic.

        Args:
            workers: List of worker instances (created by Factory)
            decision_logic: Decision logic instance (e.g., SimpleConsensus)
            parallel_workers: Enable parallel worker execution (None = auto-detect)
            parallel_threshold_ms: Min worker time to activate parallel (default: 1.0ms)
            scenario_name: Name of the scenario being executed
        """
        # ============================================
        # NEW (Issue 2): Injected dependencies
        # ============================================
        self.workers: Dict[str, AbstractBlackboxWorker] = {
            worker.name: worker for worker in workers
        }
        self.decision_logic = decision_logic
        self.strategy_config = strategy_config

        # Validate that decision logic has all required workers
        self._validate_decision_logic_requirements()

        self.is_initialized = False
        self._worker_results = {}
        self._statistics = {
            "ticks_processed": 0,
            "decisions_made": 0,
            "worker_calls": 0,
            "parallel_execution_time_saved_ms": 0.0,
        }

        # Parallelization configuration
        if parallel_workers is None:
            self.parallel_workers = self._auto_detect_parallel_mode(workers)
        else:
            self.parallel_workers = parallel_workers

        self.parallel_threshold_ms = parallel_threshold_ms
        self._avg_worker_time_ms = 0.0
        self._sample_count = 0

        # Thread pool - only create if parallel enabled
        self._thread_pool = (
            ThreadPoolExecutor(max_workers=len(workers),
                               thread_name_prefix="Worker")
            if self.parallel_workers
            else None
        )

        # ============================================
        # NEW (V0.7): Performance Logging Integration
        # ============================================
        self.performance_log = PerformanceLogCoordinator(
            scenario_name=scenario_name,
            parallel_workers=self.parallel_workers
        )

        # Create performance loggers for each worker
        for worker_name, worker in self.workers.items():
            # Extract worker type from worker parameters or name
            worker_type = self._extract_worker_type(worker)
            perf_logger = self.performance_log.create_worker_log(
                worker_type=worker_type,
                worker_name=worker_name
            )
            worker.set_performance_logger(perf_logger)

        # Create performance logger for decision logic
        decision_logic_type = self._extract_decision_logic_type(decision_logic)
        decision_perf_logger = self.performance_log.create_decision_logic_log(
            decision_logic_type=decision_logic_type,
            decision_logic_name=decision_logic.name
        )
        decision_logic.set_performance_logger(decision_perf_logger)

        # Log configuration
        vLog.debug(
            f"WorkerCoordinator config: "
            f"workers={len(self.workers)}, "
            f"decision_logic={decision_logic.name}, "
            f"parallel={self.parallel_workers}"
        )

    def _extract_worker_type(self, worker: AbstractBlackboxWorker) -> str:
        """
        Extract worker type from worker instance.

        Tries to get it from parameters or falls back to class name.

        Args:
            worker: Worker instance

        Returns:
            Worker type string (e.g., "CORE/rsi")
        """
        # Try to get from parameters
        if hasattr(worker, 'parameters') and isinstance(worker.parameters, dict):
            if 'worker_type' in worker.parameters:
                return worker.parameters['worker_type']

        # Fallback: Use class name
        class_name = worker.__class__.__name__.replace("Worker", "").lower()
        return f"CORE/{class_name}"

    def _extract_decision_logic_type(self, decision_logic: AbstractDecisionLogic) -> str:
        """
        Extract decision logic type from instance.

        Tries to get it from config or falls back to class name.

        Args:
            decision_logic: Decision logic instance

        Returns:
            Decision logic type string (e.g., "CORE/simple_consensus")
        """
        # Try to get from config
        if hasattr(decision_logic, 'config') and isinstance(decision_logic.config, dict):
            if 'decision_logic_type' in decision_logic.config:
                return decision_logic.config['decision_logic_type']

        # Fallback: Use class name
        class_name = decision_logic.__class__.__name__
        # Convert CamelCase to snake_case
        import re
        snake_case = re.sub(r'(?<!^)(?=[A-Z])', '_', class_name).lower()
        return f"CORE/{snake_case}"

    def _validate_decision_logic_requirements(self):
        """
        Validate that all required worker instances are available with correct types.

        Validation flow:
        1. Get required worker instances from DecisionLogic (instance_name → worker_type)
        2. Get configured worker_instances from config
        3. Validate:
        - All required instance names exist in config
        - All instance types match exactly (no override allowed)
        - All required instances were successfully created

        Raises:
            ValueError: If requirements not met
        """
        # Get required worker instances (instance_name → worker_type)
        required_instances = self.decision_logic.get_required_worker_instances()

        if not required_instances:
            return  # No requirements

        # Get configured worker instances
        config_instances = self.strategy_config.get("worker_instances", {})

        # Get actually created workers
        available_workers = set(self.workers.keys())

        # Validation errors
        errors = []

        # Check each required instance
        for instance_name, required_type in required_instances.items():

            # 1. Does instance exist in config?
            if instance_name not in config_instances:
                errors.append(
                    f"Missing '{instance_name}' in worker_instances. "
                    f"DecisionLogic requires this instance."
                )
                continue

            # 2. Does type match exactly?
            config_type = config_instances[instance_name]
            if config_type != required_type:
                errors.append(
                    f"Type mismatch for '{instance_name}': "
                    f"DecisionLogic requires '{required_type}', "
                    f"but config has '{config_type}'. Type override not allowed!"
                )
                continue

            # 3. Was instance successfully created?
            if instance_name not in available_workers:
                errors.append(
                    f"Worker '{instance_name}' configured but not created. "
                    f"Check factory logs for creation errors."
                )

        # Raise all errors together
        if errors:
            error_msg = (
                f"DecisionLogic '{self.decision_logic.__class__.__name__}' "
                f"validation failed:\n" + "\n".join(f"  - {e}" for e in errors)
            )
            raise ValueError(error_msg)

        vLog.debug(
            f"✓ DecisionLogic requirements validated: "
            f"{len(required_instances)} worker instances"
        )

    def _auto_detect_parallel_mode(self, workers):
        """Auto-detect parallel mode based on worker count"""
        return len(workers) >= 4

    def initialize(self):
        """Initialize coordinator and all workers"""
        vLog.debug(
            f"🔧 Initializing WorkerCoordinator with {len(self.workers)} workers "
            f"(parallel: {self.parallel_workers})"
        )

        for name, worker in self.workers.items():
            worker.set_state(WorkerState.READY)
            vLog.debug(f"  ✓ Worker '{name}' ready")

        self.is_initialized = True
        vLog.debug(
            f"✅ WorkerCoordinator initialized with DecisionLogic: {self.decision_logic.name}")

    def process_tick(
        self,
        tick: TickData,
        current_bars: Dict[str, Bar],
        bar_history: Dict[str, List[Bar]] = None,
    ) -> Decision:
        """
        Process tick through all workers and generate decision.

        ARCHITECTURE CHANGE (Issue 2):
        - Workers compute their indicators (unchanged)
        - DecisionLogic generates the trading decision (NEW!)

        ARCHITECTURE CHANGE (V0.7):
        - Performance metrics automatically tracked

        Args:
            tick: Current tick data
            current_bars: Current bars per timeframe
            bar_history: Historical bars per timeframe

        Returns:
            Decision object (from DecisionLogic)
        """
        if not self.is_initialized:
            raise RuntimeError("Coordinator not initialized")

        self._statistics["ticks_processed"] += 1
        self.performance_log.increment_ticks()
        bar_history = bar_history or {}

        # Determine if any bars were updated
        bar_updated = len(current_bars) > 0

        # Execute workers (parallel or sequential)
        if self.parallel_workers and len(self.workers) > 1:
            self._process_workers_parallel(
                tick, bar_updated, bar_history, current_bars)
        else:
            self._process_workers_sequential(
                tick, bar_updated, bar_history, current_bars
            )

        # ============================================
        # NEW (Issue 2): Delegate to DecisionLogic
        # ============================================
        # Time decision logic execution
        decision_start = time.perf_counter()

        decision = self.decision_logic.compute(
            tick=tick,
            worker_results=self._worker_results,
            current_bars=current_bars,
            bar_history=bar_history
        )

        decision_time_ms = (time.perf_counter() - decision_start) * 1000

        # Record decision logic performance
        if self.decision_logic.performance_logger:
            self.decision_logic.performance_logger.record(
                decision_time_ms, decision)

        # Update statistics
        if decision and decision.action != "FLAT":
            self._statistics["decisions_made"] += 1

        return decision

    def _process_workers_sequential(
        self,
        tick: TickData,
        bar_updated: bool,
        bar_history: Dict[str, List[Bar]],
        current_bars: Dict[str, Bar],
    ):
        """
        Process workers sequentially (original behavior).

        UNCHANGED - This method works exactly as before.
        """
        for name, worker in self.workers.items():
            if worker.should_recompute(tick, bar_updated):
                start_time = time.perf_counter()

                try:
                    worker.set_state(WorkerState.WORKING)
                    result = worker.compute(tick, bar_history, current_bars)
                    computation_time_ms = (
                        time.perf_counter() - start_time
                    ) * 1000
                    result.computation_time_ms = computation_time_ms

                    self._worker_results[name] = result
                    worker.set_state(WorkerState.READY)
                    self._statistics["worker_calls"] += 1

                    # NEW (V0.7): Record worker performance
                    if worker.performance_logger:
                        worker.performance_logger.record(computation_time_ms)

                except Exception as e:
                    vLog.error(f"❌ Worker '{name}' failed: {e}")
                    worker.set_state(WorkerState.ERROR)

    def _process_workers_parallel(
        self,
        tick: TickData,
        bar_updated: bool,
        bar_history: Dict[str, List[Bar]],
        current_bars: Dict[str, Bar],
    ):
        """
        Process workers in parallel using ThreadPoolExecutor.

        UNCHANGED - Parallelization logic works exactly as before.
        Performance boost: Workers compute simultaneously!
        """
        overall_start = time.perf_counter()

        # Collect workers that need recomputation
        workers_to_compute = [
            (name, worker)
            for name, worker in self.workers.items()
            if worker.should_recompute(tick, bar_updated)
        ]

        if not workers_to_compute:
            return

        # Submit all workers to thread pool
        future_to_worker = {}
        for name, worker in workers_to_compute:
            worker.set_state(WorkerState.WORKING)
            future = self._thread_pool.submit(
                self._compute_worker, worker, tick, bar_history, current_bars
            )
            future_to_worker[future] = (name, worker)

        # Collect results as they complete
        sequential_time_estimate = 0.0

        for future in as_completed(future_to_worker):
            name, worker = future_to_worker[future]

            try:
                result, computation_time_ms = future.result()

                self._worker_results[name] = result
                worker.set_state(WorkerState.READY)
                self._statistics["worker_calls"] += 1

                # Track sequential time for comparison
                sequential_time_estimate += computation_time_ms

                # NEW (V0.7): Record worker performance
                if worker.performance_logger:
                    worker.performance_logger.record(computation_time_ms)

            except Exception as e:
                vLog.error(
                    f"❌ Worker '{name}' failed: \n{traceback.format_exc()}")
                worker.set_state(WorkerState.ERROR)

        # Calculate time saved by parallelization
        parallel_time_ms = (time.perf_counter() - overall_start) * 1000
        time_saved = sequential_time_estimate - parallel_time_ms

        if time_saved > 0:
            self._statistics["parallel_execution_time_saved_ms"] += time_saved
            # NEW (V0.7): Record parallel performance
            self.performance_log.record_parallel_time_saved(time_saved)

    def _compute_worker(
        self,
        worker: AbstractBlackboxWorker,
        tick: TickData,
        bar_history: Dict[str, List[Bar]],
        current_bars: Dict[str, Bar],
    ) -> tuple:
        """
        Compute worker result (thread-safe helper method).

        UNCHANGED - Worker computation works exactly as before.

        Returns:
            Tuple of (result, computation_time_ms)
        """
        start_time = time.perf_counter()

        try:
            result = worker.compute(tick, bar_history, current_bars)
            computation_time_ms = (time.perf_counter() - start_time) * 1000
            result.computation_time_ms = computation_time_ms

            return result, computation_time_ms

        except Exception as e:
            raise RuntimeError(
                f"Worker {worker.name} computation failed: {e}") from e

    def get_worker_results(self) -> Dict[str, Any]:
        """
        Get all current worker results.

        UNCHANGED - This method works exactly as before.
        """
        return self._worker_results.copy()

    def get_statistics(self) -> Dict[str, Any]:
        """
        Get coordinator statistics.

        ENHANCED (V0.7): Now returns comprehensive performance metrics
        from PerformanceLogCoordinator.
        """
        # Get the full performance snapshot
        return self.performance_log.get_snapshot()

    def get_performance_snapshot(self) -> Dict[str, Any]:
        """
        Get live performance snapshot.

        NEW (V0.7): Optimized for frequent polling (TUI updates).
        Minimal overhead, designed for 300ms refresh rates.

        Returns:
            Dict with current performance metrics
        """
        return self.performance_log.get_snapshot()

    def cleanup(self):
        """
        Cleanup resources.

        UNCHANGED - Cleanup works exactly as before.
        """
        vLog.info("🧹 Cleaning up WorkerCoordinator...")

        # Shutdown thread pool
        if self._thread_pool:
            self._thread_pool.shutdown(wait=True)
            vLog.debug("  ✓ Thread pool shutdown")

        for worker in self.workers.values():
            worker.set_state(WorkerState.IDLE)

        self._worker_results.clear()
        self.is_initialized = False

        # Log final statistics
        if self.parallel_workers:
            total_saved = self._statistics["parallel_execution_time_saved_ms"]
            vLog.info(
                f"📊 Total time saved by parallelization: {total_saved:.2f}ms")
