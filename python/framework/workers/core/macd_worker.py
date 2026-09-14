"""
FiniexTestingIDE - MACD Worker
Bar-based MACD (Moving Average Convergence Divergence) computation
"""

from typing import Any, Dict, List, Optional

import numpy as np

from python.framework.types.component_metadata_types import ComponentMetadata
from python.framework.types.market_types.market_data_types import Bar, TickData
from python.framework.types.parameter_types import REQUIRED, InputParamDef, OutputParamDef
from python.framework.types.worker_types import ComputeBasis, WorkerResult, WorkerType
from python.framework.utils.trading_math.indicators.exponential_moving_average import (
    ema_warmup_bars,
)
from python.framework.utils.trading_math.indicators.macd import macd
from python.framework.workers.abstract_indicator_worker import AbstractIndicatorWorker


class MacdWorker(AbstractIndicatorWorker):
    """
    MACD (Moving Average Convergence Divergence) worker.

    Computes:
    - MACD Line: EMA(fast) - EMA(slow)
    - Signal Line: EMA(MACD, signal_period)
    - Histogram: MACD - Signal
    """

    def __init__(self, name, parameters, logger, trading_context=None):
        """
        Initialize MACD worker.
        """
        super().__init__(
            name=name, parameters=parameters,
            logger=logger, trading_context=trading_context
        )

        # Algorithm parameters (all REQUIRED in schema → guaranteed present)
        self.fast_period = self.params.get('fast_period')
        self.slow_period = self.params.get('slow_period')
        self.signal_period = self.params.get('signal_period')

        # Cross-field validation (business logic, stays in Worker)
        if self.fast_period >= self.slow_period:
            raise ValueError(
                f"MacdWorker '{self.name}': fast_period ({self.fast_period}) "
                f"must be < slow_period ({self.slow_period})"
            )

    # ============================================
    # STATIC: Classmethods for Factory/UI
    # ============================================

    @classmethod
    def get_parameter_schema(cls) -> Dict[str, InputParamDef]:
        """MACD algorithm parameters - all three periods required."""
        return {
            'fast_period': InputParamDef(
                param_type=int,
                default=REQUIRED,
                min_val=1,
                max_val=200,
                description='Fast EMA period (e.g. 12)'
            ),
            'slow_period': InputParamDef(
                param_type=int,
                default=REQUIRED,
                min_val=2,
                max_val=500,
                description='Slow EMA period (e.g. 26), must be > fast_period'
            ),
            'signal_period': InputParamDef(
                param_type=int,
                default=REQUIRED,
                min_val=1,
                max_val=200,
                description='Signal line EMA period (e.g. 9)'
            ),
        }

    @classmethod
    def get_output_schema(cls) -> Dict[str, OutputParamDef]:
        """MACD output parameters."""
        return {
            'macd': OutputParamDef(
                param_type=float,
                description='MACD line (fast EMA - slow EMA)',
                category='SIGNAL', display=True,
            ),
            'signal': OutputParamDef(
                param_type=float,
                description='Signal line (EMA of MACD line)',
                category='SIGNAL', display=True,
            ),
            'histogram': OutputParamDef(
                param_type=float,
                description='MACD histogram (MACD - signal)',
                category='SIGNAL', display=True, display_label='hist',
            ),
            'fast_ema': OutputParamDef(
                param_type=float,
                description='Fast EMA value',
            ),
            'slow_ema': OutputParamDef(
                param_type=float,
                description='Slow EMA value',
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
        """MACD is price-based — no activity-data dependency."""
        return None

    @classmethod
    def calculate_requirements(cls, config: Dict[str, Any]) -> Dict[str, int]:
        """
        Calculate MACD warmup requirements from config.

        Both averages are recursive, and the signal line is an average OF the MACD line, so
        the two warmups stack: the slow EMA must be free of its seed before the MACD values
        it produces are worth averaging, and the signal EMA then needs its own run of them.
        `slow + signal` bars — the old rule — is barely enough to compute the pair at all
        and nowhere near enough for either to have converged.

        The configured `periods` no longer sizes this. It stays in the config because it is
        what the schema requires of every INDICATOR worker, but the window this worker reads
        follows from its own three periods.

        Args:
            config: Worker configuration dict with periods, fast/slow/signal

        Returns:
            Dict[timeframe, bars_needed] - e.g. {"M5": 105} for 12/26/9
        """
        slow = config.get('slow_period')
        signal = config.get('signal_period')
        if not slow or not signal:
            return config.get('periods', {})

        needed = ema_warmup_bars(slow) + ema_warmup_bars(signal)
        return {timeframe: needed for timeframe in config.get('periods', {})}

    # ============================================
    # DYNAMIC: Instance methods for Runtime
    # ============================================

    def get_warmup_requirements(self) -> Dict[str, int]:
        """
        MACD warmup requirements.

        Delegates to the classmethod so the batch pipeline, which sizes the bar LOAD from
        it, and this instance, which is checked against it at runtime, cannot answer the
        same question differently.

        Returns:
            Dict[timeframe, bars_needed] - e.g. {"M5": 105} for 12/26/9
        """
        return self.calculate_requirements({
            'periods': self.periods,
            'fast_period': self.fast_period,
            'slow_period': self.slow_period,
            'signal_period': self.signal_period,
        })

    def get_required_timeframes(self) -> List[str]:
        """
        MACD required timeframes from config 'periods'.

        Returns:
            List of timeframes - e.g. ["M5"]
        """
        return list(self.periods.keys())

    def get_default_compute_basis(self) -> ComputeBasis:
        """LIVE — intra-bar, recompute per tick (#420). BAR_CLOSE is a per-instance opt-in."""
        return ComputeBasis.LIVE

    def should_recompute(self, tick: TickData, bar_updated: bool) -> bool:
        """MACD recomputes when bar updated"""
        return bar_updated

    def compute(
        self,
        tick: TickData,
        bar_history: Dict[str, List[Bar]],
        current_bars: Dict[str, Bar],
    ) -> WorkerResult:
        """
        MACD computation using bar close prices.

        Computes:
        1. Fast EMA and Slow EMA
        2. MACD Line = Fast EMA - Slow EMA
        3. Signal Line = EMA of MACD Line
        4. Histogram = MACD Line - Signal Line

        Works with first timeframe from 'periods' config.

        Args:
            tick: Current tick (for metadata only)
            bar_history: Historical bars per timeframe
            current_bars: Current bars per timeframe

        Returns:
            WorkerResult with MACD values
        """
        # Get first timeframe from periods
        timeframe = list(self.periods.keys())[0]

        # The window follows from the three periods, not from the configured 'periods':
        # both averages are recursive and the signal averages the MACD line, so the two
        # warmups stack. Reading a shorter window returns a seed, not a MACD.
        window = self.get_warmup_requirements()[timeframe]
        bars = self.effective_bars(timeframe, bar_history, current_bars, count=window)

        # Extract close prices from bars
        close_prices = np.array([bar.close for bar in bars[-window:]])

        values = macd(
            close_prices, self.fast_period, self.slow_period, self.signal_period)

        return WorkerResult(outputs={
            'macd': values.macd,
            'signal': values.signal,
            'histogram': values.histogram,
            'fast_ema': values.fast_ema,
            'slow_ema': values.slow_ema,
            'bars_used': float(len(close_prices)),
        })
