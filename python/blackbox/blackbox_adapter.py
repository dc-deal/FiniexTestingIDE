from typing import Dict, List, Any, Optional, Tuple, Union
import time

from python.blackbox.decision_orchestrator import DecisionOrchestrator
from python.blackbox.types import TickData

import logging

logger = logging.getLogger(__name__)


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
