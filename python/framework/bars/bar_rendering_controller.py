from python.components.logger.bootstrap_logger import setup_logging
from collections import defaultdict, deque
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set

import pandas as pd

from python.framework.bars.bar_renderer import BarRenderer
from python.framework.bars.bar_warmup_manager import BarWarmupManager
from python.framework.types import Bar, TickData, TimeframeConfig

vLog = setup_logging(name="StrategyRunner")


class BarRenderingController:
    """Main orchestrator for bar rendering system"""

    def __init__(self, data_worker):
        self.bar_renderer = BarRenderer()
        self.warmup_manager = BarWarmupManager(data_worker)
        self._workers = []
        self._required_timeframes = set()
        self._warmup_data = {}

    def register_workers(self, workers):
        """Register workers and analyze requirements"""
        self._workers = workers
        self._required_timeframes = self.bar_renderer.get_required_timeframes(
            workers)

        vLog.debug(
            f"Registered {len(workers)} workers requiring timeframes: {self._required_timeframes}"
        )

    def process_tick(self, tick_data: TickData) -> Dict[str, Bar]:
        """Process tick and update all current bars"""
        return self.bar_renderer.update_current_bars(
            tick_data, self._required_timeframes
        )

    def get_warmup_bars(self, timeframe: str) -> List[Bar]:
        """Get warmup bars for specific timeframe"""
        return self._warmup_data.get(timeframe, [])

    def get_bar_history(
        self, symbol: str, timeframe: str, count: int = 50
    ) -> List[Bar]:
        """Get bar history (completed bars)"""
        return self.bar_renderer.get_bar_history(symbol, timeframe, count)

    def get_current_bar(self, symbol: str, timeframe: str) -> Optional[Bar]:
        """Get current bar"""
        return self.bar_renderer.get_current_bar(symbol, timeframe)

    def prepare_warmup_from_ticks(
        self,
        symbol: str,
        warmup_ticks: List[TickData],
        test_start_time: datetime
    ):
        """
        Prepare warmup data from already-loaded ticks (avoids redundant data loading).
        Args:
            symbol: Trading symbol (e.g., "EURUSD")
            warmup_ticks: Pre-loaded warmup ticks from TickDataPreparator
            test_start_time: When the actual test begins
        """
        warmup_requirements = self.warmup_manager.calculate_required_warmup(
            self._workers
        )

        # Use the new method that works with pre-loaded ticks
        self._warmup_data = self.warmup_manager.prepare_warmup_from_ticks(
            symbol=symbol,
            warmup_ticks=warmup_ticks,
            warmup_requirements=warmup_requirements,
        )

        # Initialize the bar renderer's history with these warmup bars
        for timeframe, bars in self._warmup_data.items():
            self.bar_renderer.initialize_historical_bars(
                symbol, timeframe, bars)

        vLog.info(
            f"🔥 Warmup prepared with {sum(len(bars) for bars in self._warmup_data.values())} total bars (from pre-loaded ticks)"
        )
