from typing import Any, Dict, List

import numpy as np

from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.types.market_data_types import Bar, TickData
from python.framework.types.worker_types import WorkerResult, WorkerType
from python.framework.workers.abstract_worker import \
    AbstactWorker


class RSIWorker(AbstactWorker):
    """RSI computation worker - Bar-based computation"""

    def __init__(self, name: str, parameters: Dict, logger: ScenarioLogger, **kwargs):
        """
        Initialize RSI worker.

        NEW CONFIG STRUCTURE:
        {
            "periods": {"M5": 14, "M30": 14},  # REQUIRED for INDICATOR
        }

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
                f"RSIWorker '{name}' requires 'periods' in config "
                f"(e.g. {{'M5': 14}})"
            )

    @classmethod
    def get_required_parameters(cls) -> Dict[str, type]:
        """
        RSI requires 'periods' (validated by AbstactWorker).

        Returns empty because validation happens in parent class.
        """
        return {}

    @classmethod
    def get_optional_parameters(cls) -> Dict[str, Any]:
        """RSI currently has no optional parameters"""
        return {}
        # Post-MVP could have:
        # return {
        #     'smoothing': 'exponential',
        #     'overbought': 70,
        #     'oversold': 30
        # }

    @classmethod
    def get_worker_type(cls) -> WorkerType:
        return WorkerType.INDICATOR

    # ============================================
    # DYNAMIC: Instance methods für Runtime
    # ============================================

    def get_warmup_requirements(self) -> Dict[str, int]:
        """
        RSI warmup requirements from config 'periods'.

        Returns:
            Dict[timeframe, bars_needed] - e.g. {"M5": 14, "M30": 14}
        """
        return self.periods

    def get_required_timeframes(self) -> List[str]:
        """
        RSI required timeframes from config 'periods'.

        Returns:
            List of timeframes - e.g. ["M5", "M30"]
        """
        return list(self.periods.keys())

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
        """
        RSI computation using bar close prices.

        Works with first timeframe from 'periods' config.
        For multi-timeframe RSI, create multiple worker instances.
        """
        # Get first timeframe from periods
        timeframe = list(self.periods.keys())[0]
        period = self.periods[timeframe]

        # Get bar history for our timeframe
        bars = bar_history.get(timeframe, [])
        current_bar = current_bars.get(timeframe)  # Default: None
        if current_bar:  # Prüft ob Bar existiert
            bars = list(bars) + [current_bar]

        # Extract close prices from bars
        close_prices = np.array(
            [bar.close for bar in bars[-(period + 1):]])

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
        confidence = min(1.0, len(bars) / (period * 2))

        return WorkerResult(
            worker_name=self.name,
            value=float(rsi),
            confidence=confidence,
            metadata={
                "period": period,
                "timeframe": timeframe,
                "avg_gain": float(avg_gain),
                "avg_loss": float(avg_loss),
                "bars_used": len(close_prices),
            },
        )
