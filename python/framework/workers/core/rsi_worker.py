from typing import Any, Dict, List, Optional

import numpy as np

from python.framework.types.component_metadata_types import ComponentMetadata
from python.framework.types.market_types.market_data_types import Bar, TickData
from python.framework.types.parameter_types import OutputParamDef
from python.framework.types.worker_types import ComputeBasis, WorkerResult, WorkerType
from python.framework.utils.trading_math.indicators.rsi import rsi
from python.framework.utils.trading_math.indicators.wilder_moving_average import (
    rma_warmup_bars,
)
from python.framework.workers.abstract_indicator_worker import AbstractIndicatorWorker


class RsiWorker(AbstractIndicatorWorker):
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
    def get_metadata(cls) -> ComponentMetadata:
        """CORE worker metadata (version + doc pointer)."""
        return ComponentMetadata(
            version='1.0.0',
            doc_link='docs/user_guides/worker_naming_doc.md',
        )

    @classmethod
    def get_required_activity_metric(cls) -> Optional[str]:
        """RSI is price-based — no activity-data dependency."""
        return None

    @classmethod
    def get_output_schema(cls) -> Dict[str, OutputParamDef]:
        """RSI output parameters."""
        return {
            'rsi_value': OutputParamDef(
                param_type=float, min_val=0.0, max_val=100.0,
                description='RSI oscillator value',
                category='SIGNAL', display=True, display_label='rsi',
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
    # DYNAMIC: Instance methods for runtime
    # ============================================

    def get_warmup_requirements(self) -> Dict[str, int]:
        """
        RSI warmup requirements from config 'periods'.

        Wilder's smoothing is recursive, so the period is not the window: over exactly
        `period` deltas it returns its own seed — the plain mean — which is Cutler's RSI
        rather than the standard one. One extra bar turns closes into deltas.

        Returns:
            Dict[timeframe, bars_needed] - e.g. {"M5": 71, "M30": 71}
        """
        return self.calculate_requirements({'periods': self.periods})

    @classmethod
    def calculate_requirements(cls, config: Dict[str, Any]) -> Dict[str, int]:
        """
        Bars this worker needs before Wilder's smoothing is free of its seed.

        The batch pipeline sizes the bar LOAD from this classmethod while the instance is
        checked against its own requirement at runtime — so both read the same rule here.

        Args:
            config: Worker configuration dict with 'periods'

        Returns:
            Dict[timeframe, bars_needed]
        """
        return {
            timeframe: rma_warmup_bars(period) + 1
            for timeframe, period in config.get('periods', {}).items()
        }

    def get_required_timeframes(self) -> List[str]:
        """
        RSI required timeframes from config 'periods'.

        Returns:
            List of timeframes - e.g. ["M5", "M30"]
        """
        return list(self.periods.keys())

    def get_default_compute_basis(self) -> ComputeBasis:
        """LIVE — intra-bar, recompute per tick (#420). BAR_CLOSE is a per-instance opt-in."""
        return ComputeBasis.LIVE

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

        # Bars to compute on: history + the current bar unless completed-bar-only
        # (window-bounded: Wilder's smoothing reads back further than the period, and
        # one extra bar turns closes into deltas)
        window = rma_warmup_bars(period) + 1
        bars = self.effective_bars(timeframe, bar_history, current_bars, count=window)

        # Extract close prices from bars
        close_prices = np.array(
            [bar.close for bar in bars[-window:]])

        # Calculate RSI
        values = rsi(close_prices, period)

        return WorkerResult(outputs={
            'rsi_value': values.value,
            'avg_gain': values.avg_gain,
            'avg_loss': values.avg_loss,
            'bars_used': len(close_prices),
        })
