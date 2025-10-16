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

    def prepare_warmup_from_parquet_bars(
        self,
        symbol: str,
        test_start_time: datetime
    ):
        """
        Prepare warmup data - TRIES PARQUET FIRST, falls back to tick rendering.

        NEW BEHAVIOR:
        1. Try to load bars from pre-rendered parquet files (FAST!)
        2. If that fails, fall back to rendering from ticks (SLOW)

        Args:
            symbol: Trading symbol (e.g., "EURUSD")
            warmup_ticks: Pre-loaded warmup ticks from TickDataPreparator
            test_start_time: When the actual test begins
        """
        warmup_requirements = self.warmup_manager.calculate_required_warmup(
            self._workers
        )

        # === TRY PARQUET FIRST (NEW!) ===
        try:
            vLog.info(f"🚀 Attempting to load warmup bars from parquet...")
            warmup_result = self.warmup_manager.load_bars_from_parquet(
                symbol=symbol,
                warmup_requirements=warmup_requirements,
                test_start_time=test_start_time
            )

            # Extract data from result dict
            self._warmup_data = warmup_result['historical_bars']
            self._warmup_quality_metrics = warmup_result['quality_metrics']

            vLog.info(f"✅ Warmup bars loaded from parquet files!")
        except Exception as e:
            vLog.error(f"⚠️  Could not load bars from parquet: {e}")
            raise

        # Initialize the bar renderer's history with these warmup bars
        for timeframe, bars in self._warmup_data.items():
            self.bar_renderer.initialize_historical_bars(
                symbol, timeframe, bars)

        # Detailed logging per timeframe
        total_bars = sum(len(bars) for bars in self._warmup_data.values())

        vLog.info(
            f"🔥 Warmup complete: {total_bars} bars ready "
        )

    def get_warmup_quality_metrics(self) -> Dict:
        """
        Get quality metrics for warmup bars.

        Returns:
            Dict[timeframe, quality_stats] with synthetic/hybrid/real counts

        Example:
            {
                'M5': {
                    'total': 200,
                    'synthetic': 15,
                    'hybrid': 8,
                    'real': 177,
                    'synthetic_pct': 7.5,
                    'hybrid_pct': 4.0,
                    'real_pct': 88.5
                }
            }
        """
        return self._warmup_quality_metrics
