import numpy as np

from python.blackbox import WorkerContract, TickData, WorkerResult
from python.blackbox.abstract import AbstractBlackboxWorker


class RSIWorker(AbstractBlackboxWorker):
    """RSI computation worker"""

    def __init__(self, period: int = 14, **kwargs):
        super().__init__("RSI", kwargs)
        self.period = period

    def get_contract(self) -> WorkerContract:
        return WorkerContract(
            min_warmup_bars=self.period + 10,  # Extra for stability
            parameters={"rsi_period": self.period},
            price_change_sensitivity=0.0001,
            max_computation_time_ms=50.0,
        )

    def should_recompute(self, tick: TickData, history_length: int) -> bool:
        """RSI recomputes on any meaningful price change"""
        if history_length < self.period:
            return False  # Not enough data

        price_change = abs(tick.mid - self.last_processed_price)
        return price_change >= 0.0001  # 1 pip for forex

    def compute(self, tick: TickData, price_history: np.ndarray) -> WorkerResult:
        """Fast RSI computation using numpy"""

        if len(price_history) < self.period + 1:
            return WorkerResult(
                worker_name=self.name,
                value=50.0,  # Neutral RSI
                confidence=0.0,
                metadata={"insufficient_data": True},
            )

        # Get last period+1 prices for RSI calculation
        prices = price_history[-(self.period + 1) :]

        # Calculate price changes
        deltas = np.diff(prices)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)

        # Calculate averages
        avg_gain = np.mean(gains)
        avg_loss = np.mean(losses)

        if avg_loss == 0:
            rsi = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi = 100.0 - (100.0 / (1.0 + rs))

        # Confidence based on data quality
        confidence = min(1.0, len(price_history) / (self.period * 2))

        return WorkerResult(
            worker_name=self.name,
            value=float(rsi),
            confidence=confidence,
            metadata={
                "period": self.period,
                "avg_gain": float(avg_gain),
                "avg_loss": float(avg_loss),
                "data_points": len(prices),
            },
        )
