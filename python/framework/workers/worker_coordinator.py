"""
FiniexTestingIDE - Worker Coordinator (REFACTORED)
Coordinates multiple workers and delegates decision-making to DecisionLogic

ARCHITECTURE CHANGE (Issue 2):
- Workers are now injected (created by Factory)
- DecisionLogic is now injected (no hardcoded strategy)
- Coordinator only coordinates, doesn't decide

Philosophy:
- Workers are atomic units (compute indicators)
- DecisionLogic orchestrates results (makes trading decisions)
- Coordinator manages the tick-by-tick flow
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

from python.framework.decision_logic.abstract_decision_logic import \
    AbstractDecisionLogic
from python.framework.types import Bar, Decision, TickData, WorkerState
from python.framework.workers.abstract.abstract_blackbox_worker import \
    AbstractBlackboxWorker

logger = logging.getLogger(__name__)


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
        parallel_workers: bool = None,
        parallel_threshold_ms: float = 1.0,
    ):
        """
        Initialize coordinator with injected workers and decision logic.

        Args:
            workers: List of worker instances (created by Factory)
            decision_logic: Decision logic instance (e.g., SimpleConsensus)
            parallel_workers: Enable parallel worker execution (None = auto-detect)
            parallel_threshold_ms: Min worker time to activate parallel (default: 1.0ms)
        """
        # ============================================
        # NEW (Issue 2): Injected dependencies
        # ============================================
        self.workers: Dict[str, AbstractBlackboxWorker] = {
            worker.name: worker for worker in workers
        }
        self.decision_logic = decision_logic

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

        # Log configuration
        logger.debug(
            f"WorkerCoordinator config: "
            f"workers={len(self.workers)}, "
            f"decision_logic={decision_logic.name}, "
            f"parallel={self.parallel_workers}"
        )

    def _validate_decision_logic_requirements(self):
        """
        Validate that all required workers are available.

        This prevents runtime errors from missing workers.
        Called during initialization.
        """
        required_workers = self.decision_logic.get_required_workers()
        available_workers = set(self.workers.keys())

        missing = [w for w in required_workers if w not in available_workers]

        if missing:
            raise ValueError(
                f"DecisionLogic '{self.decision_logic.name}' requires workers "
                f"that are not available: {missing}. "
                f"Available workers: {list(available_workers)}"
            )

        logger.debug(
            f"✓ DecisionLogic '{self.decision_logic.name}' requirements satisfied: "
            f"{required_workers}"
        )

    def _auto_detect_parallel_mode(self, workers):
        """Auto-detect parallel mode based on worker count"""
        return len(workers) >= 4

    def initialize(self):
        """Initialize coordinator and all workers"""
        logger.debug(
            f"🔧 Initializing WorkerCoordinator with {len(self.workers)} workers "
            f"(parallel: {self.parallel_workers})"
        )

        for name, worker in self.workers.items():
            worker.set_state(WorkerState.READY)
            logger.debug(f"  ✓ Worker '{name}' ready")

        self.is_initialized = True
        logger.debug(
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
        # Generate decision using injected logic
        decision = self.decision_logic.compute(
            tick=tick,
            worker_results=self._worker_results,
            current_bars=current_bars,
            bar_history=bar_history
        )

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
                    result.computation_time_ms = (
                        time.perf_counter() - start_time
                    ) * 1000

                    self._worker_results[name] = result
                    worker.set_state(WorkerState.READY)
                    self._statistics["worker_calls"] += 1

                except Exception as e:
                    logger.error(f"❌ Worker '{name}' failed: {e}")
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

            except Exception as e:
                logger.error(f"❌ Worker '{name}' failed: {e}", exc_info=True)
                worker.set_state(WorkerState.ERROR)

        # Calculate time saved by parallelization
        parallel_time_ms = (time.perf_counter() - overall_start) * 1000
        time_saved = sequential_time_estimate - parallel_time_ms

        if time_saved > 0:
            self._statistics["parallel_execution_time_saved_ms"] += time_saved

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

        ENHANCED (Issue 2): Now includes decision logic statistics.
        """
        stats = self._statistics.copy()

        # Add parallel efficiency metrics
        if self.parallel_workers and stats["ticks_processed"] > 0:
            stats["avg_time_saved_per_tick_ms"] = (
                stats["parallel_execution_time_saved_ms"] /
                stats["ticks_processed"]
            )

        # Add decision logic statistics
        stats["decision_logic"] = self.decision_logic.get_statistics()

        return stats

    def cleanup(self):
        """
        Cleanup resources.

        UNCHANGED - Cleanup works exactly as before.
        """
        logger.info("🧹 Cleaning up WorkerCoordinator...")

        # Shutdown thread pool
        if self._thread_pool:
            self._thread_pool.shutdown(wait=True)
            logger.debug("  ✓ Thread pool shutdown")

        for worker in self.workers.values():
            worker.set_state(WorkerState.IDLE)

        self._worker_results.clear()
        self.is_initialized = False

        # Log final statistics
        if self.parallel_workers:
            total_saved = self._statistics["parallel_execution_time_saved_ms"]
            logger.info(
                f"📊 Total time saved by parallelization: {total_saved:.2f}ms")
