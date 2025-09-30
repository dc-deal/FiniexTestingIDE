"""
FiniexTestingIDE - Decision Orchestrator
Coordinates multiple workers and generates trading decisions
"""

import logging
from typing import Dict, List, Any, Optional
import time

from python.blackbox.types import TickData, Bar, WorkerState
from python.blackbox.abstract.abstract_blackbox_worker import AbstractBlackboxWorker

logger = logging.getLogger(__name__)


class DecisionOrchestrator:
    """
    Orchestrates multiple workers to generate trading decisions
    """

    def __init__(self, workers: List[AbstractBlackboxWorker]):
        """
        Initialize orchestrator with workers

        Args:
            workers: List of worker instances
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
        }

    def initialize(self):
        """Initialize orchestrator and all workers"""
        logger.info(
            f"🔧 Initializing DecisionOrchestrator with {len(self.workers)} workers"
        )

        for name, worker in self.workers.items():
            worker.set_state(WorkerState.READY)
            logger.debug(f"  ✓ Worker '{name}' ready")

        self.is_initialized = True
        logger.info("✅ DecisionOrchestrator initialized")

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

        # Update all workers that need recomputation
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

        # Generate decision based on worker results
        decision = self._generate_decision(tick)

        if decision and decision["action"] != "FLAT":
            self._statistics["decisions_made"] += 1

        return decision

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
        return self._statistics.copy()

    def cleanup(self):
        """Cleanup resources"""
        logger.info("🧹 Cleaning up DecisionOrchestrator...")
        for worker in self.workers.values():
            worker.set_state(WorkerState.IDLE)
        self._worker_results.clear()
        self.is_initialized = False
