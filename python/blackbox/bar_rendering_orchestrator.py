
import logging
from typing import Dict, List, Set, Optional
from collections import defaultdict, deque
from datetime import datetime, timedelta
import pandas as pd

from python.blackbox.types import TickData, Bar, TimeframeConfig
from python.blackbox.bar_renderer import BarRenderer
from python.blackbox.warmup_manager import WarmupManager

logger = logging.getLogger(__name__)



class BarRenderingOrchestrator:
    """Main orchestrator for bar rendering system"""

    def __init__(self, data_loader):
        self.bar_renderer = BarRenderer()
        self.warmup_manager = WarmupManager(data_loader)
        self._workers = []
        self._required_timeframes = set()
        self._warmup_data = {}

    def register_workers(self, workers):
        """Register workers and analyze requirements"""
        self._workers = workers
        self._required_timeframes = self.bar_renderer.get_required_timeframes(workers)

        logger.info(
            f"Registered {len(workers)} workers requiring timeframes: {self._required_timeframes}"
        )

    def prepare_warmup(self, symbol: str, test_start_time: datetime):
        """Prepare warmup data for all workers"""
        warmup_requirements = self.warmup_manager.calculate_required_warmup(
            self._workers
        )

        self._warmup_data = self.warmup_manager.prepare_historical_bars(
            symbol=symbol,
            test_start_time=test_start_time,
            warmup_requirements=warmup_requirements,
        )

        logger.info(
            f"Warmup prepared with {sum(len(bars) for bars in self._warmup_data.values())} total bars"
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
