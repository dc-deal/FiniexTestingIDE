"""
FiniexTestingIDE - Moving-Average Trend Worker
Bar-based trend direction + volatility-normalized slope
"""

from typing import Dict, List, Optional

import numpy as np

from python.framework.types.market_types.market_data_types import Bar, TickData
from python.framework.types.parameter_types import InputParamDef, OutputParamDef
from python.framework.types.component_metadata_types import ComponentMetadata
from python.framework.types.worker_types import ComputeBasis, WorkerResult, WorkerType
from python.framework.utils.trading_math.moving_average import moving_average
from python.framework.utils.trading_math.normalizer import Normalizer
from python.framework.workers.abstract_worker import \
    AbstractWorker


class MaTrendWorker(AbstractWorker):
    """Moving-average trend worker - direction + volatility-normalized slope"""

    def __init__(self, name, parameters, logger, trading_context=None):
        """
        Initialize MA trend worker.

        CONFIG STRUCTURE:
        {
            "periods": {"H1": 50},  # REQUIRED for INDICATOR
            "ma_type": "ema",       # Optional (sma | ema)
            "neutral_band": 0.1     # Optional
        }

        """
        super().__init__(
            name=name, parameters=parameters,
            logger=logger, trading_context=trading_context
        )

        # periods → handled by Abstract (INDICATOR type)
        # Optional — factory applies defaults; .has() guards direct construction
        self.ma_type = self.params.get('ma_type') if self.params.has('ma_type') else 'ema'
        self.neutral_band = self.params.get('neutral_band') if self.params.has('neutral_band') else 0.1

    # ============================================
    # STATIC: Classmethods for factory/UI
    # ============================================

    @classmethod
    def get_parameter_schema(cls) -> Dict[str, InputParamDef]:
        """MA trend algorithm parameters with validation ranges."""
        return {
            'ma_type': InputParamDef(
                param_type=str,
                default='ema',
                choices=('sma', 'ema'),
                description="Moving-average type for the trend line (sma or ema)"
            ),
            'neutral_band': InputParamDef(
                param_type=float,
                default=0.1,
                min_val=0.0,
                description="Slope magnitude (volatility units/bar) below which the trend is NEUTRAL"
            ),
        }

    @classmethod
    def get_output_schema(cls) -> Dict[str, OutputParamDef]:
        """MA trend output parameters."""
        return {
            'direction': OutputParamDef(
                param_type=str,
                choices=('up', 'down', 'neutral'),
                description='Trend direction from the normalized MA slope',
                category='SIGNAL', display=True, display_label='trend',
            ),
            'slope': OutputParamDef(
                param_type=float,
                description='MA slope per bar, normalized by window volatility',
                category='SIGNAL', display=True, display_label='slope',
            ),
            'ma_value': OutputParamDef(
                param_type=float,
                description='Current moving-average value',
                category='SIGNAL',
            ),
            'volatility_pct': OutputParamDef(
                param_type=float, min_val=0.0,
                description='Window std relative to the MA (regime / quiet-market read)',
                category='SIGNAL', display=True, display_label='vol',
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
    def get_metadata(cls) -> ComponentMetadata:
        """CORE worker metadata (version + doc pointer)."""
        return ComponentMetadata(
            version='1.0.0',
            doc_link='docs/user_guides/worker_naming_doc.md',
        )

    @classmethod
    def get_required_activity_metric(cls) -> Optional[str]:
        """MA trend is price-based — no activity-data dependency."""
        return None

    # ============================================
    # DYNAMIC: Instance methods for runtime
    # ============================================

    def get_warmup_requirements(self) -> Dict[str, int]:
        """
        MA trend warmup requirements from config 'periods'.

        Returns:
            Dict[timeframe, bars_needed] - e.g. {"H1": 50}
        """
        return self.periods

    def get_required_timeframes(self) -> List[str]:
        """
        MA trend required timeframes from config 'periods'.

        Returns:
            List of timeframes - e.g. ["H1"]
        """
        return list(self.periods.keys())

    def get_default_compute_basis(self) -> ComputeBasis:
        """LIVE — intra-bar, recompute per tick (#420). BAR_CLOSE is a per-instance opt-in."""
        return ComputeBasis.LIVE

    def should_recompute(self, tick: TickData, bar_updated: bool) -> bool:
        """MA trend recomputes when bar updated"""
        return bar_updated

    def compute(
        self,
        tick: TickData,
        bar_history: Dict[str, List[Bar]],
        current_bars: Dict[str, Bar],
    ) -> WorkerResult:
        """
        MA trend computation using bar close prices.

        Direction is driven by the volatility-normalized midline slope: the per-bar
        MA move expressed in units of the window's own volatility, so the neutral
        band is comparable across instruments. Works with the first timeframe from
        'periods' config.

        Args:
            tick: Current tick (for metadata only)
            bar_history: Historical bars per timeframe
            current_bars: Current bars per timeframe

        Returns:
            WorkerResult with trend direction + normalized slope
        """
        # Get first timeframe from periods
        timeframe = list(self.periods.keys())[0]
        period = self.periods[timeframe]

        # Get bar history for our timeframe
        bars = self.effective_bars(timeframe, bar_history, current_bars)

        # Extract close prices from bars (keep one extra bar for slope)
        all_closes = np.array([bar.close for bar in bars])
        close_prices = all_closes[-period:]

        ma_value = moving_average(close_prices, period, self.ma_type)
        std_window = np.std(close_prices)

        # Volatility-normalized midline slope (needs period+1 closes)
        slope = 0.0
        if len(all_closes) >= period + 1:
            prev_window = all_closes[-(period + 1):-1]
            ma_prev = moving_average(prev_window, period, self.ma_type)
            slope = Normalizer.normalize(ma_value - ma_prev, std_window)

        # Direction from the normalized slope vs the neutral band
        if slope > self.neutral_band:
            direction = 'up'
        elif slope < -self.neutral_band:
            direction = 'down'
        else:
            direction = 'neutral'

        # Relative volatility (regime / quiet-market read)
        volatility_pct = Normalizer.normalize(std_window, ma_value)

        return WorkerResult(outputs={
            'direction': direction,
            'slope': float(slope),
            'ma_value': float(ma_value),
            'volatility_pct': float(volatility_pct),
            'bars_used': len(close_prices),
        })
