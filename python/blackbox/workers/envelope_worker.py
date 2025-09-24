import numpy as np

from python.blackbox import WorkerContract, TickData, WorkerResult
from python.blackbox.abstract import AbstractBlackboxWorker


class EnvelopeWorker(AbstractBlackboxWorker):
    """Price envelope computation worker"""

    def __init__(self, period: int = 20, deviation: float = 0.02, **kwargs):
        super().__init__("Envelope", kwargs)
        self.period = period
        self.deviation = deviation

    def get_contract(self) -> WorkerContract:
        return WorkerContract(
            min_warmup_bars=self.period + 5,
            parameters={
                "envelope_period": self.period,
                "envelope_deviation": self.deviation,
            },
            price_change_sensitivity=0.0002,  # Less sensitive
            max_computation_time_ms=30.0,
        )

    def should_recompute(self, tick: TickData, history_length: int) -> bool:
        """Envelope less sensitive to price changes"""
        if history_length < self.period:
            return False

        price_change = abs(tick.mid - self.last_processed_price)
        return price_change >= 0.0002

    def compute(self, tick: TickData, price_history: np.ndarray) -> WorkerResult:
        """Fast envelope computation"""

        if len(price_history) < self.period:
            return WorkerResult(
                worker_name=self.name,
                value={"position": "neutral", "distance": 0.0},
                confidence=0.0,
            )

        # Calculate SMA using numpy
        prices = price_history[-self.period :]
        sma = np.mean(prices)

        # Calculate envelope bands
        upper_band = sma * (1 + self.deviation)
        lower_band = sma * (1 - self.deviation)

        # Determine position
        current_price = tick.mid
        if current_price > upper_band:
            position = "above"
            distance = (current_price - upper_band) / upper_band
        elif current_price < lower_band:
            position = "below"
            distance = (lower_band - current_price) / lower_band
        else:
            position = "inside"
            distance = 0.0

        confidence = min(1.0, len(price_history) / (self.period * 2))

        return WorkerResult(
            worker_name=self.name,
            value={"position": position, "distance": distance},
            confidence=confidence,
            metadata={
                "sma": float(sma),
                "upper_band": float(upper_band),
                "lower_band": float(lower_band),
                "current_price": current_price,
            },
        )
