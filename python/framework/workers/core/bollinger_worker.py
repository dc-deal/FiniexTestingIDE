"""
FiniexTestingIDE - Bollinger Worker
Bar-based Bollinger band computation
"""

from typing import Any, Dict, List, Optional

import numpy as np

from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.types.market_types.market_data_types import Bar, TickData
from python.framework.types.parameter_types import InputParamDef, OutputParamDef
from python.framework.types.component_metadata_types import ComponentMetadata
from python.framework.types.worker_types import ComputeBasis, WorkerResult, WorkerType
from python.framework.utils.trading_math.moving_average import moving_average
from python.framework.utils.trading_math.normalizer import Normalizer
from python.framework.workers.abstract_worker import \
    AbstractWorker


class BollingerWorker(AbstractWorker):
    """Bollinger Band worker - Bar-based computation"""

    def __init__(self, name, parameters, logger, trading_context=None):
        """
        Initialize Bollinger worker.

        CONFIG STRUCTURE:
        {
            "periods": {"M5": 20, "M30": 50},  # REQUIRED for INDICATOR
            "deviation": 2.0,                  # Optional
            "ma_type": "sma"                   # Optional (sma | ema)
        }

        """
        super().__init__(
            name=name, parameters=parameters,
            logger=logger, trading_context=trading_context
        )

        # periods → handled by Abstract (INDICATOR type)
        self.deviation = self.params.get('deviation')
        # Optional — factory applies the 'sma' default; .has() guards direct construction
        self.ma_type = self.params.get('ma_type') if self.params.has('ma_type') else 'sma'

    # ============================================
    # STATIC: Classmethods for factory/UI
    # ============================================

    @classmethod
    def get_parameter_schema(cls) -> Dict[str, InputParamDef]:
        """Bollinger algorithm parameters with validation ranges."""
        return {
            'deviation': InputParamDef(
                param_type=float,
                default=2.0,
                min_val=0.5,
                max_val=5.0,
                description="Standard deviation multiplier for Bollinger bands"
            ),
            'ma_type': InputParamDef(
                param_type=str,
                default='sma',
                choices=('sma', 'ema'),
                description="Moving-average type for the midline (sma or ema)"
            ),
        }

    @classmethod
    def get_output_schema(cls) -> Dict[str, OutputParamDef]:
        """Bollinger output parameters."""
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
            'position_raw': OutputParamDef(
                param_type=float,
                description='Unclamped price position (<0 below lower, >1 above upper)',
                category='SIGNAL',
            ),
            'slope': OutputParamDef(
                param_type=float,
                description='Midline slope per bar, normalized by band width',
                category='SIGNAL',
            ),
            'width_pct': OutputParamDef(
                param_type=float, min_val=0.0,
                description='Band width relative to the midline: (upper-lower)/middle',
                category='SIGNAL',
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
    def get_metadata(cls) -> ComponentMetadata:
        """CORE worker metadata (version + doc pointer)."""
        return ComponentMetadata(
            version='1.0.0',
            doc_link='docs/user_guides/worker_naming_doc.md',
        )

    @classmethod
    def get_required_activity_metric(cls) -> Optional[str]:
        """Bollinger is price-based — no activity-data dependency."""
        return None

    # ============================================
    # DYNAMIC: Instance methods for runtime
    # ============================================

    def get_warmup_requirements(self) -> Dict[str, int]:
        """
        Bollinger warmup requirements from config 'periods'.

        Returns:
            Dict[timeframe, bars_needed] - e.g. {"M5": 20, "M30": 50}
        """
        return self.periods

    def get_required_timeframes(self) -> List[str]:
        """
        Bollinger required timeframes from config 'periods'.

        Returns:
            List of timeframes - e.g. ["M5", "M30"]
        """
        return list(self.periods.keys())

    def get_default_compute_basis(self) -> ComputeBasis:
        """LIVE — `position`/`position_raw` track tick.mid intra-bar (#420). BAR_CLOSE opt-in."""
        return ComputeBasis.LIVE

    def should_recompute(self, tick: TickData, bar_updated: bool) -> bool:
        """Bollinger recomputes when bar updated"""
        return bar_updated

    def compute(
        self,
        tick: TickData,
        bar_history: Dict[str, List[Bar]],
        current_bars: Dict[str, Bar],
    ) -> WorkerResult:
        """
        Bollinger Band computation using bar close prices.

        Works with first timeframe from 'periods' config.
        For multi-timeframe Bollinger, create multiple worker instances.

        Args:
            tick: Current tick (for metadata only)
            bar_history: Historical bars per timeframe
            current_bars: Current bars per timeframe

        Returns:
            WorkerResult with Bollinger bands
        """
        # Get first timeframe from periods
        timeframe = list(self.periods.keys())[0]
        period = self.periods[timeframe]

        # Get bar history for our timeframe
        bars = self.effective_bars(timeframe, bar_history, current_bars)

        # Extract close prices from bars (keep one extra bar for slope)
        all_closes = np.array([bar.close for bar in bars])
        close_prices = all_closes[-period:]

        # Calculate Bollinger bands
        middle = moving_average(close_prices, period, self.ma_type)
        std_dev = np.std(close_prices)

        band_half = std_dev * self.deviation
        upper = middle + band_half
        lower = middle - band_half

        # Calculate current position relative to bands (raw = unclamped overshoot)
        current_price = tick.mid
        position_raw = Normalizer.rescale(current_price, lower, upper)
        position = Normalizer.clamp(position_raw)

        # Midline slope, normalized by band width (needs period+1 closes)
        band_width = upper - lower
        slope = 0.0
        if len(all_closes) >= period + 1:
            prev_window = all_closes[-(period + 1):-1]
            mid_prev = moving_average(prev_window, period, self.ma_type)
            slope = Normalizer.normalize(middle - mid_prev, band_width)

        # Band width relative to the midline
        width_pct = Normalizer.normalize(band_width, middle)

        return WorkerResult(outputs={
            'upper': float(upper),
            'middle': float(middle),
            'lower': float(lower),
            'position': float(position),
            'position_raw': float(position_raw),
            'slope': float(slope),
            'width_pct': float(width_pct),
            'std_dev': float(std_dev),
            'bars_used': len(close_prices),
        })
