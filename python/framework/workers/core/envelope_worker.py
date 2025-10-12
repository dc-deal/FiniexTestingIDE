"""
FiniexTestingIDE - Envelope Worker
Bar-based envelope/bollinger band computation
"""

from typing import Any, Dict, List

import numpy as np

from python.framework.types import Bar, TickData, WorkerResult, WorkerType
from python.framework.workers.abstract_blackbox_worker import \
    AbstractBlackboxWorker


class EnvelopeWorker(AbstractBlackboxWorker):
    """Envelope/Bollinger Band worker - Bar-based computation"""

    def __init__(self, name, parameters: Dict = None, **kwargs):
        """
        Initialize Envelope worker.

        Parameters can be provided via:
        - parameters dict (factory-style)
        - kwargs (legacy constructor-style)

        Optional parameters (all have defaults):
        - period: Moving average period (default: 20)
        - deviation: Band deviation multiplier (default: 0.02)
        - timeframe: Timeframe to use (default: "M5")
        """
        super().__init__(name, parameters)

        params = parameters or {}
        self.period = params.get('period') or kwargs.get('period', 20)
        self.deviation = params.get(
            'deviation') or kwargs.get('deviation', 0.02)
        self.timeframe = params.get(
            'timeframe') or kwargs.get('timeframe', 'M5')

    # ============================================
    # STATIC: Classmethods für Factory/UI
    # ============================================

    @classmethod
    def get_required_parameters(cls) -> Dict[str, type]:
        """Envelope hat KEINE required parameters - alle haben defaults"""
        return {}

    @classmethod
    def get_optional_parameters(cls) -> Dict[str, Any]:
        """Envelope hat NUR optionale Parameter mit defaults"""
        return {
            'period': 20,         # Moving average period
            'deviation': 0.02,    # Band deviation (2%)
            'timeframe': 'M5',    # Default timeframe
        }

    # ============================================
    # DYNAMIC: Instance methods für Runtime
    # ============================================

    def get_warmup_requirements(self) -> Dict[str, int]:
        """
        Envelope braucht 'period' bars.

        Berechnet aus self.period (aus Config!)
        """
        requirements = {}
        for tf in self.get_required_timeframes():
            requirements[tf] = self.period
        return requirements

    def get_required_timeframes(self) -> List[str]:
        """
        Envelope braucht nur einen Timeframe.

        Berechnet aus self.timeframe (aus Config!)
        """
        return [self.timeframe]

    def get_max_computation_time_ms(self) -> float:
        """Envelope ist schnell - 50ms Timeout"""
        return 50.0

    def should_recompute(self, tick: TickData, bar_updated: bool) -> bool:
        """Envelope recomputes when bar updated"""
        return bar_updated

    def compute(
        self,
        tick: TickData,
        bar_history: Dict[str, List[Bar]],
        current_bars: Dict[str, Bar],
    ) -> WorkerResult:
        """
        Envelope/Bollinger Band computation using bar close prices

        Args:
            tick: Current tick (for metadata only)
            bar_history: Historical bars per timeframe
            current_bars: Current bars per timeframe

        Returns:
            WorkerResult with envelope bands
        """

        # Get bar history for our timeframe
        bars = bar_history.get(self.timeframe, [])

        if len(bars) < self.period:
            return WorkerResult(
                worker_name=self.name,
                value={"upper": 0, "middle": 0, "lower": 0},
                confidence=0.0,
                metadata={
                    "insufficient_bars": True,
                    "bars_available": len(bars),
                    "bars_needed": self.period,
                },
            )

        # Extract close prices from bars
        close_prices = np.array([bar.close for bar in bars[-self.period:]])

        # Calculate envelope/bollinger bands
        middle = np.mean(close_prices)
        std_dev = np.std(close_prices)

        upper = middle + (std_dev * self.deviation)
        lower = middle - (std_dev * self.deviation)

        # Calculate current position relative to bands
        current_price = tick.mid
        position = 0.5  # Default middle

        if upper != lower:
            position = (current_price - lower) / (upper - lower)
            position = max(0.0, min(1.0, position))  # Clamp 0-1

        # Confidence based on bar quality
        confidence = min(1.0, len(bars) / (self.period * 2))

        return WorkerResult(
            worker_name=self.name,
            value={
                "upper": float(upper),
                "middle": float(middle),
                "lower": float(lower),
                "position": float(position),
            },
            confidence=confidence,
            metadata={
                "period": self.period,
                "timeframe": self.timeframe,
                "deviation": self.deviation,
                "std_dev": float(std_dev),
                "bars_used": len(close_prices),
                "current_price": current_price,
            },
        )
