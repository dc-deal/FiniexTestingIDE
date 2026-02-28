"""
FiniexTestingIDE - OBV Worker (On-Balance Volume)
Volume-based momentum indicator

Location: python/framework/workers/core/obv_worker.py

OBV Logic:
- If close > prev_close: OBV += volume
- If close < prev_close: OBV -= volume
- If close == prev_close: OBV unchanged

⚠️ MARKET TYPE NOTE:
- Crypto: ✅ Works (volume = traded amount in base currency)
- Forex:  ⚠️ Always 0 (CFD has no real volume) - OBV will be constant
"""

from typing import Any, Dict, List

import numpy as np

from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.types.market_data_types import Bar, TickData
from python.framework.types.market_types import TradingContext
from python.framework.types.market_config_types import MarketType
from python.framework.types.worker_types import WorkerResult, WorkerType
from python.framework.workers.abstract_worker import AbstractWorker


class OBVWorker(AbstractWorker):
    """
    On-Balance Volume (OBV) indicator worker.

    OBV is a cumulative momentum indicator that uses volume flow
    to predict changes in price direction.
    """

    def __init__(self, name, parameters, logger, trading_context=None):
        """
        Initialize OBV worker.
        """
        super().__init__(
            name=name, parameters=parameters,
            logger=logger, trading_context=trading_context
        )

        # Warn if Forex market (volume will be 0 for CFDs)
        if trading_context and trading_context.market_type == MarketType.FOREX:
            logger.warning(
                f"OBV worker '{name}' used with FOREX market. "
                f"Tick volume in Forex CFDs is typically 0 - "
                f"OBV will produce meaningless results."
            )
        self._market_type = (
            trading_context.market_type if trading_context else None
        )

    @classmethod
    def get_worker_type(cls) -> WorkerType:
        return WorkerType.INDICATOR

    # ============================================
    # Instance Methods
    # ============================================

    def get_warmup_requirements(self) -> Dict[str, int]:
        """
        OBV warmup requirements from config 'periods'.

        Returns:
            Dict[timeframe, bars_needed]
        """
        return self.periods

    def get_required_timeframes(self) -> List[str]:
        """
        OBV required timeframes from config 'periods'.

        Returns:
            List of timeframes
        """
        return list(self.periods.keys())

    def get_max_computation_time_ms(self) -> float:
        """OBV is fast - 50ms timeout"""
        return 50.0

    def should_recompute(self, tick: TickData, bar_updated: bool) -> bool:
        """OBV recomputes when bar updated (new close price)"""
        return bar_updated

    def compute(
        self,
        tick: TickData,
        bar_history: Dict[str, List[Bar]],
        current_bars: Dict[str, Bar],
    ) -> WorkerResult:
        """
        Compute OBV from bar history.

        OBV = cumulative sum of signed volumes:
        - Price up   → +volume
        - Price down → -volume
        - Price flat → 0

        Args:
            tick: Current tick data
            bar_history: Historical bars per timeframe
            current_bars: Current incomplete bars per timeframe

        Returns:
            WorkerResult with OBV value and metadata
        """
        # Get first timeframe from periods
        timeframe = list(self.periods.keys())[0]
        period = self.periods[timeframe]

        # Get bar history for our timeframe
        bars = bar_history.get(timeframe, [])
        current_bar = current_bars.get(timeframe)
        if current_bar:
            bars = list(bars) + [current_bar]

        # Need at least 2 bars for OBV calculation
        if len(bars) < 2:
            return WorkerResult(
                worker_name=self.name,
                value=0.0,
                confidence=0.0,
                metadata={
                    "period": period,
                    "timeframe": timeframe,
                    "bars_used": len(bars),
                    "error": "insufficient_bars"
                }
            )

        # Use last N bars based on period
        bars_to_use = bars[-(period + 1):] if len(bars) > period else bars

        # Extract close prices and volumes
        closes = np.array([bar.close for bar in bars_to_use])
        volumes = np.array([bar.volume for bar in bars_to_use])

        # Calculate OBV
        obv = self._calculate_obv(closes, volumes)

        # Calculate trend direction (OBV slope over last few bars)
        trend = self._calculate_trend(
            closes, volumes, min(5, len(bars_to_use) - 1))

        # Confidence based on data quality
        total_volume = float(np.sum(volumes))
        has_volume = total_volume > 0
        confidence = min(1.0, len(bars_to_use) / (period * 2)
                         ) if has_volume else 0.1

        return WorkerResult(
            worker_name=self.name,
            value=float(obv),
            confidence=confidence,
            metadata={
                "period": period,
                "timeframe": timeframe,
                "bars_used": len(bars_to_use),
                "total_volume": total_volume,
                "has_volume": has_volume,
                "trend": trend,  # "bullish", "bearish", "neutral"
                "market_type": self._market_type.value if self._market_type else None
            }
        )

    def _calculate_obv(self, closes: np.ndarray, volumes: np.ndarray) -> float:
        """
        Calculate cumulative OBV value.

        Args:
            closes: Array of close prices
            volumes: Array of volumes

        Returns:
            Final OBV value
        """
        if len(closes) < 2:
            return 0.0

        obv = 0.0
        for i in range(1, len(closes)):
            if closes[i] > closes[i - 1]:
                obv += volumes[i]
            elif closes[i] < closes[i - 1]:
                obv -= volumes[i]
            # If equal, OBV unchanged

        return obv

    def _calculate_trend(
        self,
        closes: np.ndarray,
        volumes: np.ndarray,
        lookback: int
    ) -> str:
        """
        Determine OBV trend direction.

        Args:
            closes: Array of close prices
            volumes: Array of volumes
            lookback: Number of bars to analyze

        Returns:
            "bullish", "bearish", or "neutral"
        """
        if len(closes) < lookback + 1:
            return "neutral"

        # Calculate OBV at start and end of lookback period
        start_idx = -(lookback + 1)
        obv_start = self._calculate_obv(
            closes[:start_idx], volumes[:start_idx])
        obv_end = self._calculate_obv(closes, volumes)

        diff = obv_end - obv_start

        # Threshold for trend detection (avoid noise)
        threshold = np.mean(volumes[-lookback:]) * \
            0.5 if np.any(volumes[-lookback:]) else 0

        if diff > threshold:
            return "bullish"
        elif diff < -threshold:
            return "bearish"
        else:
            return "neutral"
