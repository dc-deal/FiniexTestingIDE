from typing import Dict, List

import numpy as np

from python.framework.types import Bar, TickData, WorkerContract, WorkerResult, WorkerType
from python.framework.workers.abstract_blackbox_worker import \
    AbstractBlackboxWorker


class RSIWorker(AbstractBlackboxWorker):
    """RSI computation worker - Bar-based computation"""

    def __init__(self, name: str = "RSI", parameters: Dict = None, **kwargs):
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

    def get_contract(self) -> WorkerContract:
        """
        Define RSI worker contract.

        NEW (Issue 2): Split into required and optional parameters
        for factory validation.
        """
        return WorkerContract(
            # ============================================
            # NEW (Issue 2): Factory-Compatible Contract
            # ============================================
            worker_type=WorkerType.COMPUTE,

            # Required parameters - MUST be provided by user
            required_parameters={
                'period': int,      # RSI period (e.g., 14)
                'timeframe': str,   # Timeframe (e.g., "M5")
            },

            # Optional parameters - have defaults, can be overridden
            optional_parameters={
                # Currently none, but could add:
                # 'smoothing': 'exponential',
                # 'overbought': 70,
                # 'oversold': 30
            },

            # ============================================
            # Existing contract fields (unchanged)
            # ============================================
            parameters={'rsi_period': self.period,
                        'rsi_timeframe': self.timeframe},
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
