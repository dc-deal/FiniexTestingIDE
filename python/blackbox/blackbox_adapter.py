"""
FiniexTestingIDE - Blackbox Adapter (Enhanced)
Clean adapter with bar data integration
"""

import logging
from typing import Dict, Any, Optional, List

from python.blackbox.types import TickData, Bar
from python.blackbox.decision_orchestrator import DecisionOrchestrator

logger = logging.getLogger(__name__)


class BlackboxAdapter:
    """
    Adapter between testing framework and worker orchestrator
    Now with bar data support
    """

    def __init__(self, orchestrator: DecisionOrchestrator):
        self.orchestrator = orchestrator
        self.is_initialized = False

        # Bar data storage
        self._current_bars: Dict[str, Bar] = {}
        self._bar_history: Dict[str, List[Bar]] = {}

        # Statistics
        self._ticks_processed = 0
        self._signals_generated = 0

    def initialize(self) -> Dict[str, Any]:
        """
        Initialize adapter and orchestrator

        Returns:
            Aggregated contract information
        """
        logger.info("🔧 Initializing BlackboxAdapter...")

        self.orchestrator.initialize()
        self.is_initialized = True

        contract_info = self.get_contract_info()
        logger.info(
            f"✅ Adapter initialized with {contract_info['total_workers']} workers"
        )

        return contract_info

    def get_contract_info(self) -> Dict[str, Any]:
        """
        Get aggregated contract information from all workers

        Returns:
            Dict with contract details
        """
        contracts = []

        for worker_name, worker in self.orchestrator.workers.items():
            if hasattr(worker, "get_contract"):
                contract = worker.get_contract()
                contracts.append(
                    {
                        "worker_name": worker_name,
                        "min_warmup_bars": contract.min_warmup_bars,
                        "required_timeframes": contract.required_timeframes,
                        "parameters": contract.parameters,
                    }
                )

        # Aggregate
        max_warmup = max([c["min_warmup_bars"] for c in contracts], default=50)
        all_timeframes = list(
            set(tf for c in contracts for tf in c["required_timeframes"])
        )

        return {
            "total_workers": len(contracts),
            "max_warmup_bars": max_warmup,
            "required_timeframes": all_timeframes,
            "worker_contracts": contracts,
        }

    def set_bar_data(
        self, current_bars: Dict[str, Bar], bar_history: Dict[str, List[Bar]]
    ):
        """
        Set bar data for workers to use

        Args:
            current_bars: Dict[timeframe, Bar] - current bars per timeframe
            bar_history: Dict[timeframe, List[Bar]] - historical bars per timeframe
        """
        self._current_bars = current_bars
        self._bar_history = bar_history

    def process_tick(
        self, tick: TickData, current_bars: Optional[Dict[str, Bar]] = None
    ) -> Dict[str, Any]:
        """
        Process tick and generate decision

        Args:
            tick: TickData object
            current_bars: Optional current bars (if not already set via set_bar_data)

        Returns:
            Decision dict or None
        """
        if not self.is_initialized:
            raise RuntimeError("Adapter not initialized. Call initialize() first")

        # Use provided bars or stored bars
        if current_bars is not None:
            self._current_bars = current_bars

        self._ticks_processed += 1

        # Process through orchestrator WITH bar data
        decision = self.orchestrator.process_tick(
            tick=tick, current_bars=self._current_bars, bar_history=self._bar_history
        )

        if decision and decision.get("action") != "FLAT":
            self._signals_generated += 1

        return decision

    def get_performance_stats(self) -> Dict[str, Any]:
        """Get performance statistics"""
        return {
            "ticks_processed": self._ticks_processed,
            "signals_generated": self._signals_generated,
            "signal_rate": (
                self._signals_generated / self._ticks_processed
                if self._ticks_processed > 0
                else 0
            ),
            "worker_stats": self.orchestrator.get_statistics(),
        }

    def cleanup(self):
        """Cleanup resources"""
        logger.info("🧹 Cleaning up BlackboxAdapter...")
        self.orchestrator.cleanup()
        self._current_bars.clear()
        self._bar_history.clear()
        self.is_initialized = False
