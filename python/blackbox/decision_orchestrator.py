"""
FiniexTestingIDE - Multi-Process Blackbox Architecture
Production-ready implementation with true parallelization
"""

from typing import Dict, List, Any, Optional, Tuple, Union
from dataclasses import dataclass, field
from multiprocessing import Process, Queue, Event, shared_memory, Manager
from concurrent.futures import ProcessPoolExecutor, as_completed, TimeoutError
import numpy as np
import time
import logging
import threading
from enum import Enum
import pickle

from python.blackbox.abstract import AbstractBlackboxWorker
from python.blackbox.types import WorkerContract, WorkerState, WorkerResult, TickData

logger = logging.getLogger(__name__)


class DecisionOrchestrator:
    """
    Orchestrates multiple workers and makes final trading decision
    This is the main "brain" - the actual blackbox logic
    """

    def __init__(self, workers: List[AbstractBlackboxWorker], max_workers: int = None):
        self.workers = {worker.name: worker for worker in workers}
        self.max_workers = max_workers or len(workers)

        # Shared memory for price history
        self.price_history_shm: Optional[shared_memory.SharedMemory] = None
        self.price_array: Optional[np.ndarray] = None
        self.max_history = 1000
        self.current_history_length = 0

        # Process pool for workers
        self.executor: Optional[ProcessPoolExecutor] = None
        self.is_initialized = False

        # Decision parameters (this is the secret sauce)
        self.rsi_oversold = 30.0
        self.rsi_overbought = 70.0
        self.confidence_threshold = 0.7

    def initialize(self) -> Dict[str, Any]:
        """Initialize shared memory and worker processes"""

        # Aggregate contracts from all workers
        aggregated_contract = self._aggregate_contracts()

        # Create shared memory for price history
        self._create_shared_memory()

        # Initialize process pool
        self.executor = ProcessPoolExecutor(
            max_workers=self.max_workers,
            initializer=self._worker_initializer,
            initargs=(self.price_history_shm.name, self.max_history),
        )

        # Attach workers to shared memory in main process too
        for worker in self.workers.values():
            worker.attach_shared_memory(self.price_history_shm.name, self.max_history)

        self.is_initialized = True
        logger.info(
            f"Decision orchestrator initialized with {len(self.workers)} workers"
        )

        return aggregated_contract

    def _aggregate_contracts(self) -> Dict[str, Any]:
        """Aggregate worker contracts - this is the 'lifting' you mentioned"""

        max_warmup = 0
        all_parameters = {}

        for worker in self.workers.values():
            contract = worker.get_contract()
            max_warmup = max(max_warmup, contract.min_warmup_bars)
            all_parameters.update(contract.parameters)

        # Add decision-level parameters
        all_parameters.update(
            {
                "decision_rsi_oversold": self.rsi_oversold,
                "decision_rsi_overbought": self.rsi_overbought,
                "decision_confidence_threshold": self.confidence_threshold,
            }
        )

        return {
            "min_warmup_bars": max_warmup,
            "parameters": all_parameters,
            "worker_count": len(self.workers),
        }

    def _create_shared_memory(self):
        """Create shared memory for price history"""
        try:
            # Create shared memory buffer
            shm_size = self.max_history * 8  # 8 bytes per float64
            self.price_history_shm = shared_memory.SharedMemory(
                create=True, size=shm_size, name=f"price_history_{id(self)}"
            )

            # Create numpy array view
            self.price_array = np.ndarray(
                (self.max_history,), dtype=np.float64, buffer=self.price_history_shm.buf
            )
            self.price_array.fill(0.0)  # Initialize

            logger.debug(f"Created shared memory: {self.price_history_shm.name}")

        except Exception as e:
            logger.error(f"Failed to create shared memory: {e}")
            raise

    @staticmethod
    def _worker_initializer(shm_name: str, history_length: int):
        """Initialize worker process with shared memory"""
        # This runs in each worker process
        pass

    def update_price_history(self, tick: TickData):
        """Update shared price history"""
        if self.price_array is not None:
            # Shift array and add new price
            if self.current_history_length < self.max_history:
                # Still filling initial buffer
                self.price_array[self.current_history_length] = tick.mid
                self.current_history_length += 1
            else:
                # Circular buffer - shift left and add new price
                self.price_array[:-1] = self.price_array[1:]
                self.price_array[-1] = tick.mid

    def process_tick(
        self, tick: TickData, timeout: float = 0.1
    ) -> Optional[Dict[str, Any]]:
        """
        Process tick through all workers in parallel
        Returns trading decision or None
        """

        if not self.is_initialized:
            logger.error("Orchestrator not initialized")
            return None

        # Update price history first
        self.update_price_history(tick)

        # Submit work to all workers in parallel
        future_to_worker = {}
        for worker_name, worker in self.workers.items():
            future = self.executor.submit(self._execute_worker, worker, tick)
            future_to_worker[future] = worker_name

        # Collect results with timeout
        worker_results = {}
        completed_count = 0

        try:
            for future in as_completed(future_to_worker, timeout=timeout):
                worker_name = future_to_worker[future]
                try:
                    result = future.result()
                    worker_results[worker_name] = result
                    completed_count += 1
                except Exception as e:
                    logger.error(f"Worker {worker_name} failed: {e}")
                    # Continue with other workers

        except TimeoutError:
            logger.warning(
                f"Timeout: Only {completed_count}/{len(self.workers)} workers completed"
            )

        # Make decision with available results
        return self._make_trading_decision(tick, worker_results)

    @staticmethod
    def _execute_worker(worker: AbstractBlackboxWorker, tick: TickData) -> WorkerResult:
        """Execute single worker - runs in separate process"""
        return worker.process_tick_request(tick)

    def _make_trading_decision(
        self, tick: TickData, worker_results: Dict[str, WorkerResult]
    ) -> Dict[str, Any]:
        """
        Core trading decision logic - THE ACTUAL BLACKBOX SECRET SAUCE
        This is where your proprietary algorithm goes
        """

        # Get worker results
        rsi_result = worker_results.get("RSI")
        envelope_result = worker_results.get("Envelope")

        # Default to no action if workers failed
        if not rsi_result or not envelope_result:
            return {
                "action": "FLAT",
                "confidence": 0.0,
                "reason": "insufficient_worker_data",
                "worker_results": worker_results,
            }

        # Extract values
        rsi_value = rsi_result.value
        envelope_data = envelope_result.value

        # DECISION LOGIC (This is your secret algorithm)
        signal = "FLAT"
        confidence = 0.5
        reason = "neutral"

        # Base confidence from worker confidence
        base_confidence = min(rsi_result.confidence, envelope_result.confidence)

        if rsi_value < self.rsi_oversold and envelope_data["position"] == "below":
            # Oversold + below envelope = Strong BUY signal
            signal = "BUY"
            rsi_strength = (self.rsi_oversold - rsi_value) / self.rsi_oversold
            envelope_strength = min(envelope_data["distance"], 0.1)  # Cap at 10%
            confidence = base_confidence * (0.7 + rsi_strength + envelope_strength)
            reason = f"oversold_below_envelope_rsi_{rsi_value:.1f}"

        elif rsi_value > self.rsi_overbought and envelope_data["position"] == "above":
            # Overbought + above envelope = Strong SELL signal
            signal = "SELL"
            rsi_strength = (rsi_value - self.rsi_overbought) / (
                100 - self.rsi_overbought
            )
            envelope_strength = min(envelope_data["distance"], 0.1)
            confidence = base_confidence * (0.7 + rsi_strength + envelope_strength)
            reason = f"overbought_above_envelope_rsi_{rsi_value:.1f}"

        # Apply confidence threshold
        if confidence < self.confidence_threshold:
            signal = "FLAT"
            reason += "_low_confidence"

        return {
            "action": signal,
            "confidence": min(confidence, 1.0),
            "reason": reason,
            "metadata": {
                "rsi": rsi_value,
                "envelope_position": envelope_data["position"],
                "envelope_distance": envelope_data["distance"],
                "rsi_confidence": rsi_result.confidence,
                "envelope_confidence": envelope_result.confidence,
                "worker_computation_times": {
                    name: result.computation_time_ms
                    for name, result in worker_results.items()
                },
            },
        }

    def cleanup(self):
        """Clean up resources"""
        if self.executor:
            self.executor.shutdown(wait=True)

        if self.price_history_shm:
            self.price_history_shm.close()
            self.price_history_shm.unlink()

        logger.info("Decision orchestrator cleaned up")
