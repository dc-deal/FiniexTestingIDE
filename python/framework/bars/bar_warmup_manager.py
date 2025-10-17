import time
from python.components.logger.bootstrap_logger import get_logger
from collections import defaultdict, deque
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set

import pandas as pd

from python.framework.exceptions import InsufficientWarmupDataError
from python.framework.types import Bar, TickData, TimeframeConfig
from pathlib import Path
from python.data_worker.data_loader.parquet_bars_index import ParquetBarsIndexManager


vLog = get_logger()


class BarWarmupManager:
    """Manages historical data warmup for workers"""

    def __init__(self, data_worker, data_dir: Path = "./data/processed/"):
        self.data_worker = data_worker
        self.data_dir = Path(data_dir)

    def calculate_required_warmup(self, workers) -> Dict[str, int]:
        """Calculate maximum warmup time per timeframe"""
        warmup_requirements = defaultdict(int)

        for worker in workers:
            if hasattr(worker, "get_warmup_requirements"):
                requirements = worker.get_warmup_requirements()
                for timeframe, bars_needed in requirements.items():
                    minutes_needed = (
                        TimeframeConfig.get_minutes(timeframe) * bars_needed
                    )
                    warmup_requirements[timeframe] = max(
                        warmup_requirements[timeframe], minutes_needed
                    )

        return dict(warmup_requirements)

    def _render_historical_bars(
        self, tick_data: pd.DataFrame, timeframe: str, symbol: str
    ) -> List[Bar]:
        """Render historical bars from tick data"""
        bars = []
        current_bar = None

        for _, tick in tick_data.iterrows():
            timestamp = pd.to_datetime(tick["timestamp"])
            mid_price = (tick["bid"] + tick["ask"]) / 2.0
            volume = tick.get("volume", 0)

            bar_start_time = TimeframeConfig.get_bar_start_time(
                timestamp, timeframe)

            if (
                current_bar is None
                or pd.to_datetime(current_bar.timestamp) != bar_start_time
            ):
                if current_bar is not None:
                    current_bar.is_complete = True
                    bars.append(current_bar)

                current_bar = Bar(
                    symbol=symbol,
                    timeframe=timeframe,
                    timestamp=bar_start_time.isoformat(),
                    open=0,
                    high=0,
                    low=0,
                    close=0,
                    volume=0,
                )

            current_bar.update_with_tick(mid_price, volume)

        if current_bar is not None:
            current_bar.is_complete = True
            bars.append(current_bar)

        return bars

    def load_bars_from_parquet(
        self,
        symbol: str,
        test_start_time: datetime,
        warmup_requirements: Dict[str, int],
    ) -> Dict:
        """
        Load warmup bars from pre-rendered parquet files.

        THIS IS THE NEW FAST PATH! 🚀
        Instead of rendering bars from ticks (60s), we load pre-rendered bars (<100ms).

        Args:
            symbol: Trading symbol (e.g., 'EURUSD')
            warmup_requirements: Dict[timeframe, minutes_needed]
            data_dir: Root data directory

        Returns:
            Dict[timeframe, List[Bar]] - Warmup bars per timeframe

        Raises:
            InsufficientWarmupDataError: If bars not found or insufficient
        """
        vLog.info(f"📊 Loading warmup bars from pre-rendered parquet files...")

        # Initialize bar index
        bar_index = ParquetBarsIndexManager(Path(self.data_dir))
        bar_index.build_index()

        historical_bars = {}
        quality_metrics = {}

        for timeframe, minutes_needed in warmup_requirements.items():
            # Calculate bars needed
            bars_needed = minutes_needed // TimeframeConfig.get_minutes(
                timeframe)

            # Get bar file from index
            bar_file = bar_index.get_bar_file(symbol, timeframe)

            if not bar_file:
                raise InsufficientWarmupDataError(
                    timeframe=timeframe,
                    required_bars=bars_needed,
                    rendered_bars=0,
                    last_bar_timestamp=None
                )

            # Load bars from parquet
            bars_df = pd.read_parquet(bar_file)

            # Take last N bars needed for warmup
            # === CRITICAL FIX ===
            # Filter: Only bars BEFORE test_start_time!
            # We need warmup bars that happened BEFORE the test starts
            bars_before_test = bars_df[bars_df['timestamp'] < test_start_time]

            if len(bars_before_test) < bars_needed:
                raise InsufficientWarmupDataError(
                    timeframe=timeframe,
                    required_bars=bars_needed,
                    rendered_bars=len(bars_before_test),
                    last_bar_timestamp=bars_before_test['timestamp'].max(
                    ).isoformat() if len(bars_before_test) > 0 else None
                )

            # Take last N bars before test start
            warmup_bars_df = bars_before_test.tail(bars_needed)

            # === NEW: Analyze bar quality ===
            quality = self._analyze_bar_quality(warmup_bars_df)
            quality_metrics[timeframe] = quality

            # Convert DataFrame to Bar objects
            bars = []
            for _, row in warmup_bars_df.iterrows():
                bar = Bar(
                    symbol=row['symbol'],
                    timeframe=row['timeframe'],
                    timestamp=row['timestamp'].isoformat(),
                    open=float(row['open']),
                    high=float(row['high']),
                    low=float(row['low']),
                    close=float(row['close']),
                    volume=float(row['volume']),
                    tick_count=int(row['tick_count']),
                    is_complete=True
                )
                bars.append(bar)

            historical_bars[timeframe] = bars

            vLog.debug(
                f"✅ Loaded {len(bars)} {timeframe} bars from parquet "
                f"(required: {bars_needed})"
            )

        total_bars = sum(len(bars) for bars in historical_bars.values())
        timeframe_details = ", ".join(
            [f"{tf}:{len(bars)}" for tf, bars in historical_bars.items()]
        )

        vLog.info(
            f"🔥 Warmup complete: {total_bars} bars loaded from parquet "
            f"({timeframe_details})"
        )

        return {
            'historical_bars': historical_bars,
            'quality_metrics': quality_metrics
        }

    def _analyze_bar_quality(self, bars_df: pd.DataFrame) -> Dict:
        """
        Analyze quality of warmup bars (synthetic/hybrid/real).

        Args:
            bars_df: DataFrame with warmup bars

        Returns:
            Dict with quality statistics
        """
        total = len(bars_df)

        # Count bar types
        synthetic = len(bars_df[bars_df['bar_type'] == 'synthetic'])
        hybrid = len(bars_df[bars_df['bar_type'] == 'hybrid'])
        real = len(bars_df[bars_df['bar_type'] == 'real'])

        return {
            'total': total,
            'synthetic': synthetic,
            'hybrid': hybrid,
            'real': real,
            'synthetic_pct': round((synthetic / total * 100), 2) if total > 0 else 0.0,
            'hybrid_pct': round((hybrid / total * 100), 2) if total > 0 else 0.0,
            'real_pct': round((real / total * 100), 2) if total > 0 else 0.0
        }
