"""
FiniexTestingIDE - Heavy Workers for Parallelization Tests
Workers with artificial CPU load for testing parallel performance
"""

import time
from typing import Any, Dict, List

import numpy as np

from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.types.market_data_types import Bar, TickData
from python.framework.types.parameter_types import ParameterDef
from python.framework.types.worker_types import WorkerResult, WorkerType
from python.framework.workers.abstract_worker import \
    AbstactWorker


class HeavyRSIWorker(AbstactWorker):
    """
    RSI worker with artificial CPU load.
    Simulates complex calculations (e.g., ML models, FFT, etc.)
    """

    def __init__(self, name, parameters, logger, trading_context=None):
        """
        Heavy RSI Worker with artificial CPU load.
        """
        super().__init__(
            name=name, parameters=parameters,
            logger=logger, trading_context=trading_context
        )

        self.artificial_load_ms = self.params.get('artificial_load_ms')

    @classmethod
    def get_parameter_schema(cls) -> Dict[str, ParameterDef]:
        """Heavy RSI algorithm parameters with artificial load config."""
        return {
            'artificial_load_ms': ParameterDef(
                param_type=float,
                default=5.0,
                min_val=0.0,
                max_val=1000.0,
                description="Artificial CPU load in milliseconds for parallel testing"
            ),
        }

    @classmethod
    def get_worker_type(cls) -> WorkerType:
        return WorkerType.INDICATOR

    def get_warmup_requirements(self) -> Dict[str, int]:
        """Heavy RSI warmup requirements from config 'periods'"""
        return self.periods

    def get_required_timeframes(self) -> List[str]:
        """Heavy RSI required timeframes from config 'periods'"""
        return list(self.periods.keys())

    def get_max_computation_time_ms(self) -> float:
        return self.artificial_load_ms + 10.0

    def should_recompute(self, tick: TickData, bar_updated: bool) -> bool:
        return bar_updated

    def compute(
        self,
        tick: TickData,
        bar_history: Dict[str, List[Bar]],
        current_bars: Dict[str, Bar],
    ) -> WorkerResult:
        """
        RSI computation with artificial CPU load.

        Works with first timeframe from 'periods' config.
        """
        # === ARTIFICIAL LOAD (CPU-intensive) ===
        self._simulate_heavy_computation()

        # Get first timeframe from periods
        timeframe = list(self.periods.keys())[0]
        period = self.periods[timeframe]

        # === NORMAL RSI CALCULATION ===
        bars = bar_history.get(timeframe, [])
        current_bar = current_bars.get(timeframe)  # Default: None (not [])
        if current_bar:  # Check if Bar exists (not Dict!)
            bars = list(bars) + [current_bar]

        close_prices = np.array(
            [bar.close for bar in bars[-(period + 1):]])
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

        confidence = min(1.0, len(bars) / (period * 2))

        return WorkerResult(
            worker_name=self.name,
            value=float(rsi),
            confidence=confidence,
            metadata={
                "artificial_load_ms": self.artificial_load_ms,
                "period": period,
                "timeframe": timeframe,
            },
        )

    def _simulate_heavy_computation(self):
        """Simulates CPU-intensive calculations (matrix ops)"""
        start = time.perf_counter()
        target_duration = self.artificial_load_ms / 1000.0

        size = 50
        while (time.perf_counter() - start) < target_duration:
            matrix_a = np.random.rand(size, size)
            matrix_b = np.random.rand(size, size)
            result = np.dot(matrix_a, matrix_b)
            result = np.sin(result) + np.cos(result)
            _ = result.sum()
