"""
FiniexTestingIDE - Decision Orchestrator (PARALLEL VERSION)
Coordinates multiple workers and generates trading decisions
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

from python.framework.types import Bar, TickData, WorkerState
from python.framework.workers.abstract.abstract_blackbox_worker import \
    AbstractBlackboxWorker

logger = logging.getLogger(__name__)


class WorkerCoordinator:
    """
    Orchestrates multiple workers to generate trading decisions
    """

    def __init__(
        self,
        workers: List[AbstractBlackboxWorker],
        parallel_workers: bool = None,  # ← None = Auto-detect
        parallel_threshold_ms: float = 1.0,
    ):
        """
        Initialize orchestrator with workers

        Args:
            workers: List of worker instances
            parallel_workers: Enable parallel worker execution (None = auto-detect)
            parallel_threshold_ms: Min worker time to activate parallel (default: 1.0ms)
        """
        self.workers: Dict[str, AbstractBlackboxWorker] = {
            worker.name: worker for worker in workers
        }

        self.is_initialized = False
        self._worker_results = {}
        self._statistics = {
            "ticks_processed": 0,
            "decisions_made": 0,
            "worker_calls": 0,
            "parallel_execution_time_saved_ms": 0.0,
        }

        # Parallelization configuration
        # Auto-detect parallel mode if not specified
        if parallel_workers is None:
            # Smart defaults für unerfahrene User
            self.parallel_workers = self._auto_detect_parallel_mode(workers)
        else:
            # Explicit override vom Scenario
            self.parallel_workers = parallel_workers

        self.parallel_threshold_ms = parallel_threshold_ms
        self._avg_worker_time_ms = 0.0
        self._sample_count = 0

        # Thread pool - only create if parallel enabled
        self._thread_pool = (
            ThreadPoolExecutor(max_workers=len(workers),
                               thread_name_prefix="Worker")
            if parallel_workers
            else None
        )

        # Log configuration
        logger.debug(
            f"WorkerCoordinator config: "
            f"parallel={parallel_workers}, "
            f"threshold={parallel_threshold_ms}ms"
        )

    def _auto_detect_parallel_mode(self, workers):
        # Heuristik: 4+ workers → parallel
        return len(workers) >= 4

    def initialize(self):
        """Initialize orchestrator and all workers"""
        logger.info(
            f"🔧 Initializing WorkerCoordinator with {len(self.workers)} workers "
            f"(parallel: {self.parallel_workers})"
        )

        for name, worker in self.workers.items():
            worker.set_state(WorkerState.READY)
            logger.debug(f"  ✓ Worker '{name}' ready")

        self.is_initialized = True
        logger.info("✅ WorkerCoordinator initialized")

    def process_tick(
        self,
        tick: TickData,
        current_bars: Dict[str, Bar],
        bar_history: Dict[str, List[Bar]] = None,
    ) -> Dict[str, Any]:
        """
        Process tick through all workers and generate decision

        Args:
            tick: Current tick data
            current_bars: Current bars per timeframe
            bar_history: Historical bars per timeframe

        Returns:
            Decision dict with action/confidence/reason
        """
        if not self.is_initialized:
            raise RuntimeError("Orchestrator not initialized")

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

        # Generate decision based on worker results
        decision = self._generate_decision(tick)

        if decision and decision["action"] != "FLAT":
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
        Process workers sequentially (original behavior)
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
        Process workers in parallel using ThreadPoolExecutor

        PERFORMANCE BOOST: Workers compute simultaneously!
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
        Compute worker result (thread-safe helper method)

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
            # Re-raise to be handled by caller
            raise RuntimeError(
                f"Worker {worker.name} computation failed: {e}") from e

    def _generate_decision(self, tick: TickData) -> Dict[str, Any]:
        """
        Generate trading decision based on worker results

        Simple RSI + Envelope strategy logic:
        - RSI < 30 + Price near lower envelope = BUY
        - RSI > 70 + Price near upper envelope = SELL
        """

        # Get worker results
        rsi_result = self._worker_results.get("RSI")
        envelope_result = self._worker_results.get("Envelope")

        # Need both workers
        if not rsi_result or not envelope_result:
            return {"action": "FLAT", "reason": "Insufficient worker data"}

        if rsi_result.confidence < 0.5 or envelope_result.confidence < 0.5:
            return {"action": "FLAT", "reason": "Low confidence"}

        # Extract values
        rsi = rsi_result.value
        envelope = envelope_result.value

        # Trading logic
        action = "FLAT"
        reason = "No clear signal"
        confidence = 0.5

        # BUY signal: RSI oversold + price near lower band
        if rsi <= 30 and envelope["position"] < 0.3:
            action = "BUY"
            reason = f"RSI oversold ({rsi:.1f}) + price near lower band"
            confidence = min(rsi_result.confidence, envelope_result.confidence)

        # SELL signal: RSI overbought + price near upper band
        elif rsi >= 70 and envelope["position"] > 0.7:
            action = "SELL"
            reason = f"RSI overbought ({rsi:.1f}) + price near upper band"
            confidence = min(rsi_result.confidence, envelope_result.confidence)

        return {
            "action": action,
            "price": tick.mid,
            "timestamp": tick.timestamp,
            "confidence": confidence,
            "reason": reason,
            "metadata": {
                "rsi": rsi,
                "envelope_position": envelope["position"],
                "envelope_upper": envelope["upper"],
                "envelope_lower": envelope["lower"],
            },
        }

    def get_worker_results(self) -> Dict[str, Any]:
        """Get all current worker results"""
        return self._worker_results.copy()

    def get_statistics(self) -> Dict[str, Any]:
        """Get orchestrator statistics"""
        stats = self._statistics.copy()

        # Add parallel efficiency metrics
        if self.parallel_workers and stats["ticks_processed"] > 0:
            stats["avg_time_saved_per_tick_ms"] = (
                stats["parallel_execution_time_saved_ms"] /
                stats["ticks_processed"]
            )

        return stats

    def cleanup(self):
        """Cleanup resources"""
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
