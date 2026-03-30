from typing import Any, Dict, List

import numpy as np

from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.types.market_types.market_data_types import Bar, TickData
from python.framework.types.parameter_types import OutputParamDef
from python.framework.types.worker_types import WorkerResult, WorkerType
from python.framework.workers.abstract_worker import \
    AbstractWorker


class RsiWorker(AbstractWorker):
    """RSI computation worker - Bar-based computation"""

    def __init__(self, name, parameters, logger, trading_context=None):
        """
        Initialize RSI worker.
        """
        super().__init__(
            name=name, parameters=parameters,
            logger=logger, trading_context=trading_context
        )

    @classmethod
    def get_worker_type(cls) -> WorkerType:
        return WorkerType.INDICATOR

    @classmethod
    def get_output_schema(cls) -> Dict[str, OutputParamDef]:
        """RSI output parameters."""
        return {
            'rsi_value': OutputParamDef(
                param_type=float, min_val=0.0, max_val=100.0,
                description='RSI oscillator value',
                category='SIGNAL', display=True,
            ),
            'avg_gain': OutputParamDef(
                param_type=float, min_val=0.0,
                description='Average gain over period',
            ),
            'avg_loss': OutputParamDef(
                param_type=float, min_val=0.0,
                description='Average loss over period',
            ),
            'bars_used': OutputParamDef(
                param_type=int, min_val=0,
                description='Number of bars used in calculation',
            ),
        }

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

        return WorkerResult(outputs={
            'rsi_value': float(rsi),
            'avg_gain': float(avg_gain),
            'avg_loss': float(avg_loss),
            'bars_used': len(close_prices),
        })
