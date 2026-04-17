"""
FiniexTestingIDE - Envelope Worker
Bar-based envelope/bollinger band computation
"""

from typing import Any, Dict, List, Optional

import numpy as np

from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.types.market_types.market_data_types import Bar, TickData
from python.framework.types.parameter_types import InputParamDef, OutputParamDef
from python.framework.types.worker_types import WorkerResult, WorkerType
from python.framework.workers.abstract_worker import \
    AbstractWorker


class EnvelopeWorker(AbstractWorker):
    """Envelope/Bollinger Band worker - Bar-based computation"""

    def __init__(self, name, parameters, logger, trading_context=None):
        """
        Initialize Envelope worker.

        CONFIG STRUCTURE:
        {
            "periods": {"M5": 20, "M30": 50},  # REQUIRED for INDICATOR
            "deviation": 2.0                   # Optional
        }

        """
        super().__init__(
            name=name, parameters=parameters,
            logger=logger, trading_context=trading_context
        )

        # periods → handled by Abstract (INDICATOR type)
        self.deviation = self.params.get('deviation')

    # ============================================
    # STATIC: Classmethods für Factory/UI
    # ============================================

    @classmethod
    def get_parameter_schema(cls) -> Dict[str, InputParamDef]:
        """Envelope algorithm parameters with validation ranges."""
        return {
            'deviation': InputParamDef(
                param_type=float,
                default=2.0,
                min_val=0.5,
                max_val=5.0,
                description="Standard deviation multiplier for Bollinger bands"
            ),
        }

    @classmethod
    def get_output_schema(cls) -> Dict[str, OutputParamDef]:
        """Envelope output parameters."""
        return {
            'upper': OutputParamDef(
                param_type=float,
                description='Upper band value',
                category='SIGNAL', display=True, display_label='up',
            ),
            'middle': OutputParamDef(
                param_type=float,
                description='Middle band (SMA)',
                category='SIGNAL',
            ),
            'lower': OutputParamDef(
                param_type=float,
                description='Lower band value',
                category='SIGNAL', display=True, display_label='lo',
            ),
            'position': OutputParamDef(
                param_type=float, min_val=0.0, max_val=1.0,
                description='Price position within bands (0=lower, 1=upper)',
                category='SIGNAL', display=True, display_label='pos',
            ),
            'std_dev': OutputParamDef(
                param_type=float, min_val=0.0,
                description='Standard deviation used for band width',
            ),
            'bars_used': OutputParamDef(
                param_type=int, min_val=0,
                description='Number of bars used in calculation',
            ),
        }

    @classmethod
    def get_worker_type(cls) -> WorkerType:
        return WorkerType.INDICATOR

    @classmethod
    def get_required_activity_metric(cls) -> Optional[str]:
        """Envelope/Bollinger is price-based — no activity-data dependency."""
        return None

    # ============================================
    # DYNAMIC: Instance methods für Runtime
    # ============================================

    def get_warmup_requirements(self) -> Dict[str, int]:
        """
        Envelope warmup requirements from config 'periods'.

        Returns:
            Dict[timeframe, bars_needed] - e.g. {"M5": 20, "M30": 50}
        """
        return self.periods

    def get_required_timeframes(self) -> List[str]:
        """
        Envelope required timeframes from config 'periods'.

        Returns:
            List of timeframes - e.g. ["M5", "M30"]
        """
        return list(self.periods.keys())

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
        Envelope/Bollinger Band computation using bar close prices.

        Works with first timeframe from 'periods' config.
        For multi-timeframe envelope, create multiple worker instances.

        Args:
            tick: Current tick (for metadata only)
            bar_history: Historical bars per timeframe
            current_bars: Current bars per timeframe

        Returns:
            WorkerResult with envelope bands
        """
        # Get first timeframe from periods
        timeframe = list(self.periods.keys())[0]
        period = self.periods[timeframe]

        # Get bar history for our timeframe
        bars = bar_history.get(timeframe, [])
        current_bar = current_bars.get(timeframe)  # Default: None (not [])
        if current_bar:  # Check if Bar exists (not Dict!)
            bars = list(bars) + [current_bar]

        # Extract close prices from bars
        close_prices = np.array([bar.close for bar in bars[-period:]])

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

        return WorkerResult(outputs={
            'upper': float(upper),
            'middle': float(middle),
            'lower': float(lower),
            'position': float(position),
            'std_dev': float(std_dev),
            'bars_used': len(close_prices),
        })
