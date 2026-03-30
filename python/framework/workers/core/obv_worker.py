"""
FiniexTestingIDE - OBV Worker (On-Balance Volume)
Volume-based momentum indicator

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
from python.framework.types.market_types.market_data_types import Bar, TickData
from python.framework.types.market_types.market_types import TradingContext
from python.framework.types.market_types.market_config_types import MarketType
from python.framework.types.parameter_types import OutputParamDef
from python.framework.types.worker_types import WorkerResult, WorkerType
from python.framework.workers.abstract_worker import AbstractWorker


class ObvWorker(AbstractWorker):
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

    @classmethod
    def get_output_schema(cls) -> Dict[str, OutputParamDef]:
        """OBV output parameters."""
        return {
            'obv_value': OutputParamDef(
                param_type=float,
                description='On-Balance Volume cumulative value',
                category='SIGNAL', display=True,
            ),
            'trend': OutputParamDef(
                param_type=str,
                description='OBV trend direction',
                choices=('bullish', 'bearish', 'neutral'),
            ),
            'has_volume': OutputParamDef(
                param_type=bool,
                description='Whether volume data is available',
            ),
            'total_volume': OutputParamDef(
                param_type=float, min_val=0.0,
                description='Total volume over period',
            ),
            'bars_used': OutputParamDef(
                param_type=int, min_val=0,
                description='Number of bars used in calculation',
            ),
            'market_type': OutputParamDef(
                param_type=str,
                description='Market type (crypto, forex)',
                choices=('crypto', 'forex'),
            ),
        }

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
            return WorkerResult(outputs={
                'obv_value': 0.0,
                'trend': 'neutral',
                'has_volume': False,
                'total_volume': 0.0,
                'bars_used': len(bars),
                'market_type': self._market_type.value if self._market_type else None,
            })

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

        total_volume = float(np.sum(volumes))
        has_volume = total_volume > 0

        return WorkerResult(outputs={
            'obv_value': float(obv),
            'trend': trend,
            'has_volume': has_volume,
            'total_volume': total_volume,
            'bars_used': len(bars_to_use),
            'market_type': self._market_type.value if self._market_type else None,
        })

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
