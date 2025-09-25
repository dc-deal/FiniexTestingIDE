import numpy as np
from typing import List, Dict

from python.blackbox.types import WorkerContract, TickData, WorkerResult, Bar
from python.blackbox.abstract import AbstractBlackboxWorker


class RSIWorker(AbstractBlackboxWorker):
    """RSI computation worker - Bar-based computation"""

    def __init__(self, period: int = 14, timeframe: str = "M5", **kwargs):
        self.period = period
        self.timeframe = timeframe
        super().__init__("RSI", kwargs)

    def get_contract(self) -> WorkerContract:
        return WorkerContract(
            min_warmup_bars=self.period + 10,
            parameters={"rsi_period": self.period, "rsi_timeframe": self.timeframe},
            price_change_sensitivity=0.0001,
            max_computation_time_ms=50.0,
            required_timeframes=self.get_required_timeframes(),
            warmup_requirements=self.get_warmup_requirements(),
        )

    def get_required_timeframes(self) -> List[str]:
        """Define timeframes needed"""
        return [self.timeframe]

    def should_recompute(self, tick: TickData, bar_updated: bool) -> bool:
        """RSI recomputes when bar updated"""
        return bar_updated

    def compute(
        self,
        tick: TickData,
        bar_history: Dict[str, List[Bar]],
        current_bars: Dict[str, Bar],
    ) -> WorkerResult:
        """RSI computation using bar close prices"""

        # Get bar history for our timeframe
        bars = bar_history.get(self.timeframe, [])

        if len(bars) < self.period + 1:
            return WorkerResult(
                worker_name=self.name,
                value=50.0,
                confidence=0.0,
                metadata={"insufficient_bars": True, "bars_available": len(bars)},
            )

        # Extract close prices from bars
        close_prices = np.array([bar.close for bar in bars[-(self.period + 1) :]])

        # Calculate RSI
        deltas = np.diff(close_prices)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)

        avg_gain = np.mean(gains)
        avg_loss = np.mean(losses)

        if avg_loss == 0:
            rsi = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi = 100.0 - (100.0 / (1.0 + rs))

        # Confidence based on bar quality
        confidence = min(1.0, len(bars) / (self.period * 2))

        return WorkerResult(
            worker_name=self.name,
            value=float(rsi),
            confidence=confidence,
            metadata={
                "period": self.period,
                "timeframe": self.timeframe,
                "avg_gain": float(avg_gain),
                "avg_loss": float(avg_loss),
                "bars_used": len(close_prices),
            },
        )
