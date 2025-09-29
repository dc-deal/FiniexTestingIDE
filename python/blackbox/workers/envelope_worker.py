"""
FiniexTestingIDE - Envelope Worker
Bar-based envelope/bollinger band computation
"""

import numpy as np
from typing import List, Dict

from python.blackbox.types import WorkerContract, TickData, WorkerResult, Bar
from python.blackbox.abstract import AbstractBlackboxWorker


class EnvelopeWorker(AbstractBlackboxWorker):
    """Envelope/Bollinger Band worker - Bar-based computation"""

    def __init__(
        self, period: int = 20, deviation: float = 0.02, timeframe: str = "M5", **kwargs
    ):
        # FIX: Set instance variables BEFORE super().__init__
        self.period = period
        self.deviation = deviation
        self.timeframe = timeframe

        super().__init__("Envelope", kwargs)

    def get_contract(self) -> WorkerContract:
        """Define worker contract"""

        return WorkerContract(
            parameters={
                "envelope_period": self.period,
                "envelope_deviation": self.deviation,
                "envelope_timeframe": self.timeframe,
            },
            price_change_sensitivity=0.0001,
            max_computation_time_ms=50.0,
            required_timeframes=self.get_required_timeframes(),
            warmup_requirements=self.get_warmup_requirements(),
        )

    def get_warmup_requirements(self):
        requirements = {}
        minimum_warmup_bars = self.period + 10
        for tf in self.get_required_timeframes():
            requirements[tf] = minimum_warmup_bars
        return requirements

    def get_required_timeframes(self) -> List[str]:
        """Define required timeframes"""
        return [self.timeframe]

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
        close_prices = np.array([bar.close for bar in bars[-self.period :]])

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
