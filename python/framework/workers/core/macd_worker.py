"""
FiniexTestingIDE - MACD Worker
Bar-based MACD (Moving Average Convergence Divergence) computation
"""

from typing import Any, Dict, List

import numpy as np

from python.components.logger.scenario_logger import ScenarioLogger
from python.framework.types.market_data_types import Bar, TickData
from python.framework.types.worker_types import WorkerResult, WorkerType
from python.framework.workers.abstract_worker import AbstactWorker


class MACDWorker(AbstactWorker):
    """
    MACD (Moving Average Convergence Divergence) worker.

    Computes:
    - MACD Line: EMA(fast) - EMA(slow)
    - Signal Line: EMA(MACD, signal_period)
    - Histogram: MACD - Signal
    """

    def __init__(self, name: str, parameters: Dict, logger: ScenarioLogger, **kwargs):
        """
        Initialize MACD worker.

        NEW CONFIG STRUCTURE:
        {
            "periods": {"M5": 35},     # REQUIRED - warmup bars (auto-calculated)
            "fast_period": 12,         # Required - fast EMA period
            "slow_period": 26,         # Required - slow EMA period
            "signal_period": 9         # Required - signal line period
        }

        Warmup calculation: max(fast, slow) + signal
        Example: max(12, 26) + 9 = 35 bars needed

        Parameters can be provided via:
        - parameters dict (factory-style)
        - kwargs (legacy constructor-style)
        """
        super().__init__(name=name, parameters=parameters, logger=logger, **kwargs)

        params = parameters or {}

        # Extract 'periods' namespace (REQUIRED for INDICATOR)
        self.periods = params.get('periods', kwargs.get('periods', {}))

        if not self.periods:
            raise ValueError(
                f"MACDWorker '{name}' requires 'periods' in config "
                f"(e.g. {{'M5': 35}})"
            )

        # Extract MACD-specific parameters (required)
        self.fast_period = params.get('fast_period', kwargs.get('fast_period'))
        self.slow_period = params.get('slow_period', kwargs.get('slow_period'))
        self.signal_period = params.get(
            'signal_period', kwargs.get('signal_period'))

        # Validate MACD parameters
        if not all([self.fast_period, self.slow_period, self.signal_period]):
            raise ValueError(
                f"MACDWorker '{name}' requires fast_period, slow_period, "
                f"and signal_period"
            )

        if self.fast_period >= self.slow_period:
            raise ValueError(
                f"MACDWorker '{name}': fast_period ({self.fast_period}) must be "
                f"< slow_period ({self.slow_period})"
            )

    # ============================================
    # STATIC: Classmethods for Factory/UI
    # ============================================

    @classmethod
    def get_required_parameters(cls) -> Dict[str, type]:
        """
        MACD requires 'periods' (validated by AbstactWorker).
        Also requires MACD-specific parameters.

        Returns empty for 'periods' because validation happens in parent class.
        """
        return {
            'fast_period': int,   # Fast EMA period (e.g., 12)
            'slow_period': int,   # Slow EMA period (e.g., 26)
            'signal_period': int,  # Signal line period (e.g., 9)
        }

    @classmethod
    def get_optional_parameters(cls) -> Dict[str, Any]:
        """MACD has no optional parameters currently"""
        return {}

    @classmethod
    def get_worker_type(cls) -> WorkerType:
        return WorkerType.INDICATOR

    @classmethod
    def calculate_requirements(cls, config: Dict[str, Any]) -> Dict[str, int]:
        """
        Calculate MACD warmup requirements from config.

        MACD needs: max(fast_period, slow_period) + signal_period bars
        This ensures enough data for both MACD line and signal line.

        Args:
            config: Worker configuration dict with periods, fast/slow/signal

        Returns:
            Dict[timeframe, bars_needed] - e.g. {"M5": 35}

        Example:
            >>> config = {
            ...     "periods": {"M5": 35},
            ...     "fast_period": 12,
            ...     "slow_period": 26,
            ...     "signal_period": 9
            ... }
            >>> MACDWorker.calculate_requirements(config)
            {"M5": 35}
        """
        # Use periods directly from config
        return config.get("periods", {})

    # ============================================
    # DYNAMIC: Instance methods for Runtime
    # ============================================

    def get_warmup_requirements(self) -> Dict[str, int]:
        """
        MACD warmup requirements from config 'periods'.

        Returns:
            Dict[timeframe, bars_needed] - e.g. {"M5": 35}
        """
        return self.periods

    def get_required_timeframes(self) -> List[str]:
        """
        MACD required timeframes from config 'periods'.

        Returns:
            List of timeframes - e.g. ["M5"]
        """
        return list(self.periods.keys())

    def get_max_computation_time_ms(self) -> float:
        """MACD is moderately fast - 75ms timeout"""
        return 75.0

    def should_recompute(self, tick: TickData, bar_updated: bool) -> bool:
        """MACD recomputes when bar updated"""
        return bar_updated

    def compute(
        self,
        tick: TickData,
        bar_history: Dict[str, List[Bar]],
        current_bars: Dict[str, Bar],
    ) -> WorkerResult:
        """
        MACD computation using bar close prices.

        Computes:
        1. Fast EMA and Slow EMA
        2. MACD Line = Fast EMA - Slow EMA
        3. Signal Line = EMA of MACD Line
        4. Histogram = MACD Line - Signal Line

        Works with first timeframe from 'periods' config.

        Args:
            tick: Current tick (for metadata only)
            bar_history: Historical bars per timeframe
            current_bars: Current bars per timeframe

        Returns:
            WorkerResult with MACD values
        """
        # Get first timeframe from periods
        timeframe = list(self.periods.keys())[0]
        period = self.periods[timeframe]

        # Get bar history for our timeframe
        bars = bar_history.get(timeframe, [])
        current_bar = current_bars.get(timeframe)
        if current_bar:
            bars = list(bars) + [current_bar]

        # Extract close prices from bars
        close_prices = np.array([bar.close for bar in bars[-period:]])

        # Calculate EMAs
        fast_ema = self._calculate_ema(close_prices, self.fast_period)
        slow_ema = self._calculate_ema(close_prices, self.slow_period)

        # Calculate MACD line
        macd_line = fast_ema - slow_ema

        # Calculate signal line (EMA of MACD line)
        # For signal line, we need MACD values, not close prices
        # Simplified: use last few MACD values if we have enough bars
        if len(bars) >= self.slow_period + self.signal_period:
            # Calculate historical MACD values for signal line
            macd_values = []
            for i in range(self.signal_period, len(close_prices) + 1):
                hist_close = close_prices[:i]
                hist_fast = self._calculate_ema(hist_close, self.fast_period)
                hist_slow = self._calculate_ema(hist_close, self.slow_period)
                macd_values.append(hist_fast - hist_slow)

            signal_line = self._calculate_ema(
                np.array(macd_values), self.signal_period
            )
        else:
            # Not enough data for signal line yet
            signal_line = macd_line

        # Calculate histogram
        histogram = macd_line - signal_line

        # Confidence based on bar quality
        required_bars = max(
            self.fast_period, self.slow_period) + self.signal_period
        confidence = min(1.0, len(bars) / (required_bars * 1.5))

        return WorkerResult(
            worker_name=self.name,
            value={
                "macd": float(macd_line),
                "signal": float(signal_line),
                "histogram": float(histogram),
                "fast_ema": float(fast_ema),
                "slow_ema": float(slow_ema),
            },
            confidence=confidence,
            metadata={
                "fast_period": self.fast_period,
                "slow_period": self.slow_period,
                "signal_period": self.signal_period,
                "timeframe": timeframe,
                "bars_used": len(close_prices),
            },
        )

    def _calculate_ema(self, prices: np.ndarray, period: int) -> float:
        """
        Calculate Exponential Moving Average.

        Args:
            prices: Array of prices
            period: EMA period

        Returns:
            Current EMA value
        """
        if len(prices) < period:
            # Not enough data, return simple average
            return np.mean(prices)

        # Calculate EMA using standard formula
        multiplier = 2 / (period + 1)
        ema = np.mean(prices[:period])  # Start with SMA

        for price in prices[period:]:
            ema = (price - ema) * multiplier + ema

        return ema
