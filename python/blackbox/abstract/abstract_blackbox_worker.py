from abc import ABC, abstractmethod
from typing import Dict, List, Any, Optional, Tuple, Union
import logging
from multiprocessing import Process, Queue, Event, shared_memory, Manager
from concurrent.futures import ProcessPoolExecutor, as_completed, TimeoutError
import numpy as np
import time

from python.blackbox.types import TickData, WorkerContract, WorkerResult, WorkerState


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
