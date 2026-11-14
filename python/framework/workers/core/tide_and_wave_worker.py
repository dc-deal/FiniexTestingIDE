"""
FiniexTestingIDE - Tide and Wave Worker
The first two screens of Alexander Elder's Triple Screen (Trading for a Living, 1993) as an
indicator, on two timeframes: the tide — the MACD histogram and its slope on the longer one — and
the wave — an oscillator on the shorter one, read against the tide. Elder puts the two about five
times apart; the wave is read here with the RSI the indicator library defines.

An indicator only: which side the tide allows and when the wave enters against it are a trading
method, and they belong to a decision logic, not to this worker.
"""

from typing import Any, Dict, List, Mapping, Optional, Tuple

import numpy as np

from python.framework.trading_env.algo_masks.algo_bar import AlgoBar
from python.framework.trading_env.algo_masks.algo_tick import AlgoTick
from python.framework.types.chart_types.indicator_series_types import (
    FilledArea,
    IndicatorPane,
    IndicatorSeriesDeclaration,
    LineStyle,
    Plot,
    PlotKind,
    PlotOptions,
    PriceFormat,
    PriceLine,
    ValueRange,
)
from python.framework.types.component_metadata_types import ComponentMetadata
from python.framework.types.market_types.market_data_types import Bar
from python.framework.types.parameter_types import InputParamDef, OutputParamDef
from python.framework.types.worker_types import ComputeBasis, WorkerResult, WorkerType
from python.framework.utils.timeframe_config_utils import TimeframeConfig
from python.framework.utils.trading_math.indicators.exponential_moving_average import (
    ema_warmup_bars,
)
from python.framework.utils.trading_math.indicators.macd import macd
from python.framework.utils.trading_math.indicators.rsi import rsi
from python.framework.utils.trading_math.indicators.wilder_moving_average import (
    rma_warmup_bars,
)
from python.framework.workers.abstract_indicator_worker import AbstractIndicatorWorker

# Where each screen stands in the configured 'periods': the wave first, the tide second
_WAVE_POSITION = 0
_TIDE_POSITION = 1


class TideAndWaveWorker(AbstractIndicatorWorker):
    """The tide on the longer timeframe and the wave on the shorter one."""

    def __init__(self, name, parameters, logger, trading_context=None):
        """
        Initialize the tide and wave worker.
        """
        super().__init__(
            name=name, parameters=parameters,
            logger=logger, trading_context=trading_context
        )
        self.fast_period = self.params.get('fast_period')
        self.slow_period = self.params.get('slow_period')
        self.signal_period = self.params.get('signal_period')
        if self.fast_period >= self.slow_period:
            raise ValueError(
                f"TideAndWaveWorker '{self.name}': fast_period ({self.fast_period}) "
                f"must be < slow_period ({self.slow_period})"
            )

    # ============================================
    # STATIC: Classmethods for Factory/UI
    # ============================================

    @classmethod
    def validate_config(cls, config: Dict[str, Any]) -> None:
        """
        Refuse a configuration without exactly two timeframes, the tide's longer than the wave's.

        Args:
            config: Worker configuration dict

        Returns:
            None
        """
        super().validate_config(config)
        timeframes = list(config.get('periods', {}))
        if len(timeframes) != 2:
            raise ValueError(
                f"Worker '{cls.__name__}': 'periods' names two timeframes — the wave's first, "
                f"the tide's second — got {timeframes}")
        wave, tide = timeframes
        if TimeframeConfig.get_minutes(tide) <= TimeframeConfig.get_minutes(wave):
            raise ValueError(
                f"Worker '{cls.__name__}': the tide's timeframe ({tide}) must be longer than "
                f"the wave's ({wave}) — Elder puts them about five times apart")

    @classmethod
    def get_parameter_schema(cls) -> Dict[str, InputParamDef]:
        """The tide's MACD periods; the wave's RSI period is its timeframe's entry in 'periods'."""
        return {
            'fast_period': InputParamDef(
                param_type=int, default=12, min_val=1, max_val=200,
                description="The tide's fast EMA period"
            ),
            'slow_period': InputParamDef(
                param_type=int, default=26, min_val=2, max_val=500,
                description="The tide's slow EMA period, longer than fast_period"
            ),
            'signal_period': InputParamDef(
                param_type=int, default=9, min_val=1, max_val=200,
                description="The tide's signal line EMA period"
            ),
        }

    @classmethod
    def get_indicator_series(cls) -> Tuple[IndicatorSeriesDeclaration, ...]:
        """
        The wave on its fixed 0–100 scale with 70 and 30, and the tide's histogram from zero in a
        pane of its own, on the longer timeframe.

        Returns:
            The series
        """
        return (
            IndicatorSeriesDeclaration(
                pane=IndicatorPane.OWN, title='Wave', price_format=PriceFormat(precision=2),
                range=ValueRange(min_value=0, max_value=100),
                plots=(Plot(id='wave_rsi'),),
                price_lines=(PriceLine(price=70, title='70'), PriceLine(price=30, title='30')),
                filled_areas=(FilledArea(between=('price_line:70', 'price_line:30')),),
                timeframe_position=_WAVE_POSITION),
            IndicatorSeriesDeclaration(
                pane=IndicatorPane.OWN, title='Tide', layer_name='tide',
                plots=(Plot(id='tide_histogram', kind=PlotKind.HISTOGRAM,
                            options=PlotOptions(base=0.0)),),
                price_lines=(PriceLine(price=0, line_style=LineStyle.DOTTED),),
                timeframe_position=_TIDE_POSITION),
        )

    @classmethod
    def get_output_schema(cls) -> Dict[str, OutputParamDef]:
        """The wave and the tide."""
        return {
            'wave_rsi': OutputParamDef(
                param_type=float, min_val=0.0, max_val=100.0,
                description='The wave: RSI on the shorter timeframe',
                category='SIGNAL', display=True, display_label='wave',
            ),
            'tide_histogram': OutputParamDef(
                param_type=float,
                description='The tide: MACD histogram on the longer timeframe',
                category='SIGNAL', display=True, display_label='tide',
            ),
            'tide_slope': OutputParamDef(
                param_type=float,
                description="The tide's direction: the histogram's change from the bar before",
                category='SIGNAL', display=True, display_label='slope',
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
        """Both screens are price-based — no activity-data dependency."""
        return None

    @classmethod
    def calculate_requirements(cls, config: Dict[str, Any]) -> Dict[str, int]:
        """
        Bars each screen needs: the wave's Wilder smoothing free of its seed plus one bar for the
        deltas; the tide's two stacked EMAs free of theirs, plus one bar for the slope.

        Args:
            config: Worker configuration dict with periods and the tide's three periods

        Returns:
            Dict[timeframe, bars_needed]
        """
        timeframes = list(config.get('periods', {}))
        if len(timeframes) != 2:
            return {}
        wave, tide = timeframes
        slow = config.get('slow_period', cls.get_parameter_schema()['slow_period'].default)
        signal = config.get('signal_period', cls.get_parameter_schema()['signal_period'].default)
        return {
            wave: rma_warmup_bars(config['periods'][wave]) + 1,
            tide: ema_warmup_bars(slow) + ema_warmup_bars(signal) + 1,
        }

    # ============================================
    # DYNAMIC: Instance methods for Runtime
    # ============================================

    def get_warmup_requirements(self) -> Dict[str, int]:
        """
        Delegates to the classmethod, so the bar load and the runtime check read one rule.

        Returns:
            Dict[timeframe, bars_needed]
        """
        return self.calculate_requirements({
            'periods': self.periods,
            'slow_period': self.slow_period,
            'signal_period': self.signal_period,
        })

    def get_required_timeframes(self) -> List[str]:
        """
        The wave's timeframe, then the tide's.

        Returns:
            List of timeframes - e.g. ["M5", "M30"]
        """
        return list(self.periods.keys())

    def get_default_compute_basis(self) -> ComputeBasis:
        """LIVE — intra-bar, recompute per tick (#420). BAR_CLOSE is a per-instance opt-in."""
        return ComputeBasis.LIVE

    def should_recompute(self, tick: AlgoTick, bar_updated: bool) -> bool:
        """Recomputes when a bar updated"""
        return bar_updated

    def compute(
        self,
        tick: AlgoTick,
        bar_history: Mapping[str, Tuple[Bar, ...]],
        current_bars: Mapping[str, AlgoBar],
    ) -> WorkerResult:
        """
        The wave's RSI on the first timeframe, the tide's MACD histogram and its slope on the
        second.

        Args:
            tick: Current tick (for metadata only)
            bar_history: Historical bars per timeframe
            current_bars: Current bars per timeframe

        Returns:
            WorkerResult with the wave and the tide
        """
        timeframes = self.get_required_timeframes()
        wave_timeframe = timeframes[_WAVE_POSITION]
        tide_timeframe = timeframes[_TIDE_POSITION]
        windows = self.get_warmup_requirements()

        wave_window = windows[wave_timeframe]
        wave_bars = self.effective_bars(wave_timeframe, bar_history, current_bars,
                                        count=wave_window)
        wave = rsi(np.array([bar.close for bar in wave_bars[-wave_window:]]),
                   self.periods[wave_timeframe])

        tide_window = windows[tide_timeframe]
        tide_bars = self.effective_bars(tide_timeframe, bar_history, current_bars,
                                        count=tide_window)
        tide_closes = np.array([bar.close for bar in tide_bars[-tide_window:]])
        tide = macd(tide_closes, self.fast_period, self.slow_period, self.signal_period)
        before = macd(tide_closes[:-1], self.fast_period, self.slow_period, self.signal_period)

        return WorkerResult(outputs={
            'wave_rsi': wave.value,
            'tide_histogram': tide.histogram,
            'tide_slope': tide.histogram - before.histogram,
        })
