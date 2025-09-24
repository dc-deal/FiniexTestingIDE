"""
FiniexTestingIDE - Multi-Process Blackbox Architecture
Production-ready implementation with true parallelization
"""

from abc import ABC, abstractmethod
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

from types import WorkerContract, WorkerState, WorkerResult, TickData

logger = logging.getLogger(__name__)


class AbstractBlackboxWorker(ABC):
    """
    Abstract worker that runs in separate process
    Key: Worker decides autonomously when to recompute
    """

    def __init__(self, name: str, config: Dict[str, Any] = None):
        self.name = name
        self.config = config or {}
        self.last_processed_price = 0.0
        self.last_result: Optional[WorkerResult] = None
        self.state = WorkerState.IDLE

        # Shared memory will be set by orchestrator
        self.price_history_shm: Optional[shared_memory.SharedMemory] = None
        self.price_array: Optional[np.ndarray] = None
        self.history_length = 0

    @abstractmethod
    def get_contract(self) -> WorkerContract:
        """Define what this worker needs and provides"""
        pass

    @abstractmethod
    def should_recompute(self, tick: TickData, history_length: int) -> bool:
        """Worker decides if recomputation needed for this tick"""
        pass

    @abstractmethod
    def compute(self, tick: TickData, price_history: np.ndarray) -> WorkerResult:
        """Core computation logic"""
        pass

    def attach_shared_memory(self, shm_name: str, history_length: int):
        """Attach to shared price history"""
        try:
            self.price_history_shm = shared_memory.SharedMemory(name=shm_name)
            self.price_array = np.ndarray(
                (history_length,), dtype=np.float64, buffer=self.price_history_shm.buf
            )
            self.history_length = history_length
            logger.debug(f"Worker {self.name} attached to shared memory")
        except Exception as e:
            logger.error(f"Worker {self.name} failed to attach shared memory: {e}")
            raise

    def process_tick_request(self, tick: TickData) -> WorkerResult:
        """
        Main entry point for tick processing
        Returns immediately with either new result or cached result
        """

        start_time = time.time()

        try:
            # Check if recomputation needed
            if not self.should_recompute(
                tick, len(self.price_array) if self.price_array is not None else 0
            ):
                # Return cached result (marked as stale)
                if self.last_result:
                    stale_result = WorkerResult(
                        worker_name=self.name,
                        value=self.last_result.value,
                        confidence=self.last_result.confidence
                        * 0.95,  # Slight confidence penalty
                        computation_time_ms=0.0,
                        metadata={**self.last_result.metadata, "cached": True},
                        is_stale=True,
                    )
                    return stale_result

            self.state = WorkerState.WORKING

            # Perform computation
            result = self.compute(tick, self.price_array)
            result.computation_time_ms = (time.time() - start_time) * 1000

            # Cache result
            self.last_result = result
            self.last_processed_price = tick.mid
            self.state = WorkerState.READY

            return result

        except Exception as e:
            self.state = WorkerState.ERROR
            logger.error(f"Worker {self.name} computation failed: {e}")

            # Return error result
            return WorkerResult(
                worker_name=self.name,
                value=None,
                confidence=0.0,
                computation_time_ms=(time.time() - start_time) * 1000,
                metadata={"error": str(e)},
            )


class RSIWorker(AbstractBlackboxWorker):
    """RSI computation worker"""

    def __init__(self, period: int = 14, **kwargs):
        super().__init__("RSI", kwargs)
        self.period = period

    def get_contract(self) -> WorkerContract:
        return WorkerContract(
            min_warmup_bars=self.period + 10,  # Extra for stability
            parameters={"rsi_period": self.period},
            price_change_sensitivity=0.0001,
            max_computation_time_ms=50.0,
        )

    def should_recompute(self, tick: TickData, history_length: int) -> bool:
        """RSI recomputes on any meaningful price change"""
        if history_length < self.period:
            return False  # Not enough data

        price_change = abs(tick.mid - self.last_processed_price)
        return price_change >= 0.0001  # 1 pip for forex

    def compute(self, tick: TickData, price_history: np.ndarray) -> WorkerResult:
        """Fast RSI computation using numpy"""

        if len(price_history) < self.period + 1:
            return WorkerResult(
                worker_name=self.name,
                value=50.0,  # Neutral RSI
                confidence=0.0,
                metadata={"insufficient_data": True},
            )

        # Get last period+1 prices for RSI calculation
        prices = price_history[-(self.period + 1) :]

        # Calculate price changes
        deltas = np.diff(prices)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)

        # Calculate averages
        avg_gain = np.mean(gains)
        avg_loss = np.mean(losses)

        if avg_loss == 0:
            rsi = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi = 100.0 - (100.0 / (1.0 + rs))

        # Confidence based on data quality
        confidence = min(1.0, len(price_history) / (self.period * 2))

        return WorkerResult(
            worker_name=self.name,
            value=float(rsi),
            confidence=confidence,
            metadata={
                "period": self.period,
                "avg_gain": float(avg_gain),
                "avg_loss": float(avg_loss),
                "data_points": len(prices),
            },
        )


class EnvelopeWorker(AbstractBlackboxWorker):
    """Price envelope computation worker"""

    def __init__(self, period: int = 20, deviation: float = 0.02, **kwargs):
        super().__init__("Envelope", kwargs)
        self.period = period
        self.deviation = deviation

    def get_contract(self) -> WorkerContract:
        return WorkerContract(
            min_warmup_bars=self.period + 5,
            parameters={
                "envelope_period": self.period,
                "envelope_deviation": self.deviation,
            },
            price_change_sensitivity=0.0002,  # Less sensitive
            max_computation_time_ms=30.0,
        )

    def should_recompute(self, tick: TickData, history_length: int) -> bool:
        """Envelope less sensitive to price changes"""
        if history_length < self.period:
            return False

        price_change = abs(tick.mid - self.last_processed_price)
        return price_change >= 0.0002

    def compute(self, tick: TickData, price_history: np.ndarray) -> WorkerResult:
        """Fast envelope computation"""

        if len(price_history) < self.period:
            return WorkerResult(
                worker_name=self.name,
                value={"position": "neutral", "distance": 0.0},
                confidence=0.0,
            )

        # Calculate SMA using numpy
        prices = price_history[-self.period :]
        sma = np.mean(prices)

        # Calculate envelope bands
        upper_band = sma * (1 + self.deviation)
        lower_band = sma * (1 - self.deviation)

        # Determine position
        current_price = tick.mid
        if current_price > upper_band:
            position = "above"
            distance = (current_price - upper_band) / upper_band
        elif current_price < lower_band:
            position = "below"
            distance = (lower_band - current_price) / lower_band
        else:
            position = "inside"
            distance = 0.0

        confidence = min(1.0, len(price_history) / (self.period * 2))

        return WorkerResult(
            worker_name=self.name,
            value={"position": position, "distance": distance},
            confidence=confidence,
            metadata={
                "sma": float(sma),
                "upper_band": float(upper_band),
                "lower_band": float(lower_band),
                "current_price": current_price,
            },
        )


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


class BlackboxAdapter:
    """
    High-level adapter that manages the complete blackbox system
    This is what the testing engine will interface with
    """

    def __init__(self, orchestrator: DecisionOrchestrator):
        self.orchestrator = orchestrator
        self.contract_info = None
        self.is_ready = False

        # Performance tracking
        self.stats = {
            "ticks_processed": 0,
            "decisions_made": 0,
            "avg_processing_time_ms": 0.0,
            "worker_timeout_count": 0,
        }

    def initialize(self) -> Dict[str, Any]:
        """Initialize the complete blackbox system"""

        self.contract_info = self.orchestrator.initialize()
        self.is_ready = True

        logger.info("Blackbox adapter ready for trading")
        return self.contract_info

    def feed_warmup_data(self, warmup_ticks: List[TickData]):
        """Feed warmup data to build price history"""

        logger.info(f"Feeding {len(warmup_ticks)} warmup ticks")

        for tick in warmup_ticks:
            self.orchestrator.update_price_history(tick)

        logger.info("Warmup complete")

    def process_tick(self, tick: TickData) -> Optional[Dict[str, Any]]:
        """Process single tick and get trading decision"""

        if not self.is_ready:
            logger.error("Blackbox not initialized")
            return None

        start_time = time.time()

        # Get trading decision
        decision = self.orchestrator.process_tick(tick)

        # Update stats
        processing_time = (time.time() - start_time) * 1000
        self.stats["ticks_processed"] += 1
        self.stats["avg_processing_time_ms"] = (
            self.stats["avg_processing_time_ms"] * (self.stats["ticks_processed"] - 1)
            + processing_time
        ) / self.stats["ticks_processed"]

        if decision and decision["action"] != "FLAT":
            self.stats["decisions_made"] += 1

        return decision

    def get_performance_stats(self) -> Dict[str, Any]:
        """Get performance statistics"""
        decision_rate = 0.0
        if self.stats["ticks_processed"] > 0:
            decision_rate = self.stats["decisions_made"] / self.stats["ticks_processed"]

        return {
            **self.stats,
            "decision_rate": decision_rate,
            "throughput_ticks_per_sec": 1000
            / max(self.stats["avg_processing_time_ms"], 0.001),
        }

    def cleanup(self):
        """Clean up resources"""
        self.orchestrator.cleanup()
        self.is_ready = False


# Am Ende von multiprocess_architecture.py
if __name__ == "__main__":
    print("🏗️ FiniexTestingIDE Blackbox Architecture")
    print("This module contains the core framework classes.")
    print("For testing, use: python python/blackbox/strategy_runner.py")
