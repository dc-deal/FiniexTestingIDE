from typing import Any, Dict, List

import numpy as np

from python.framework.types.global_types import Bar, TickData, WorkerResult, WorkerType
from python.framework.workers.abstract_blackbox_worker import \
    AbstractBlackboxWorker


class RSIWorker(AbstractBlackboxWorker):
    """RSI computation worker - Bar-based computation"""

    def __init__(self, name: str, parameters: Dict = None, **kwargs):
        """
        Initialize RSI worker.

        Parameters can be provided via:
        - parameters dict (factory-style)
        - kwargs (legacy constructor-style)

        Required parameters:
        - period: RSI calculation period
        - timeframe: Timeframe to use
        """
        super().__init__(name, parameters)

        # Extract parameters from dict or kwargs
        params = parameters or {}
        self.period = params.get('period') or kwargs.get('period', 14)
        self.timeframe = params.get(
            'timeframe') or kwargs.get('timeframe', 'M5')

    @classmethod
    def get_required_parameters(cls) -> Dict[str, type]:
        """RSI benötigt period und timeframe zwingend"""
        return {
            'period': int,      # RSI period (e.g., 14)
            'timeframe': str,   # Timeframe (e.g., "M5")
        }

    @classmethod
    def get_optional_parameters(cls) -> Dict[str, Any]:
        """RSI hat aktuell keine optionalen Parameter"""
        return {}
        # Post-MVP könnte hier stehen:
        # return {
        #     'smoothing': 'exponential',
        #     'overbought': 70,
        #     'oversold': 30
        # }

    # ============================================
    # DYNAMIC: Instance methods für Runtime
    # ============================================

    def get_warmup_requirements(self) -> Dict[str, int]:
        """
        RSI braucht exakt 'period' bars.

        Berechnet aus self.period (aus Config!)
        """
        requirements = {}
        for tf in self.get_required_timeframes():
            requirements[tf] = self.period
        return requirements

    def get_required_timeframes(self) -> List[str]:
        """
        RSI braucht nur einen Timeframe.

        Berechnet aus self.timeframe (aus Config!)
        """
        return [self.timeframe]

    def get_max_computation_time_ms(self) -> float:
        """RSI ist schnell - 50ms Timeout"""
        return 50.0

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
                metadata={"insufficient_bars": True,
                          "bars_available": len(bars)},
            )

        # Extract close prices from bars
        close_prices = np.array(
            [bar.close for bar in bars[-(self.period + 1):]])

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
