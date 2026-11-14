"""
FiniexTestingIDE - Indicator Series Entry Types (#379)

One worker instance's indicator series as a run records it and the viewer receives it: what the
worker declared, the layer and pane it is drawn in, the parameters for the legend, the timeframe its
values belong to, and the values themselves — one per bar at the bar's opening instant, in parallel
arrays. A bar with no value yet, during the warmup, is null and never left out, so the arrays stay
aligned with the bars; nothing is filled forward.
"""

from datetime import datetime
from typing import Dict, List, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

from python.framework.types.chart_types.indicator_series_types import IndicatorSeriesShape

# A legend input is a plain value: the period behind 'RSI 14', a deviation, a mode
IndicatorInput = Union[bool, int, float, str]


class IndicatorSeriesLayer(IndicatorSeriesShape):
    """
    One series as a run draws it — what the worker declared, in the layer and pane of its instance.

    Args:
        layer: 'worker:<instance>', or 'worker:<instance>:<name>' for a further series of the same
            worker — one row in the viewer's Layers panel, its plots switched together
        pane: 'price' for the candles' pane; any other name is a pane of its own, one per name
        inputs: The parameters the legend shows after the title
        timeframe: The timeframe whose bars the values belong to
    """
    layer: str = Field(min_length=1)
    pane: str = Field(min_length=1)
    inputs: Dict[str, IndicatorInput] = {}
    timeframe: str


class IndicatorSeriesEntry(IndicatorSeriesLayer):
    """
    One series with its values, served.

    Args:
        times: The bars' opening instants, in UTC, as the bars route serves them
        data: Per plot id, one value per entry of `times`; null where the bar has none
    """
    times: List[datetime] = []
    data: Dict[str, List[Optional[float]]] = {}

    @model_validator(mode='after')
    def _values_align_with_the_bars(self) -> 'IndicatorSeriesEntry':
        """
        Refuse values for a plot the series does not declare, a plot without values, and an array
        that does not have one value per bar.

        Returns:
            The entry
        """
        plot_ids = {plot.id for plot in self.plots}
        if set(self.data) != plot_ids:
            raise ValueError(f'{self.layer}: data holds {sorted(self.data)}, the plots are '
                             f'{sorted(plot_ids)}')
        for plot_id, values in self.data.items():
            if len(values) != len(self.times):
                raise ValueError(f'{self.layer}: {plot_id} has {len(values)} values for '
                                 f'{len(self.times)} bars')
        return self


class IndicatorSeriesStreamHeader(BaseModel):
    """
    The fields of an indicator series stream's header line, beside the stream's own.

    Args:
        unit_name: The session, or the backtest scenario, that recorded it
        worker: The worker instance whose outputs it holds
        cadence_timeframe: The finest of the timeframes its series are drawn on — every record
            falls on its grid
        series: The worker's series, in the order they are drawn, each with its own timeframe
    """
    model_config = ConfigDict(frozen=True, extra='ignore')

    unit_name: str
    worker: str
    cadence_timeframe: str
    series: List[IndicatorSeriesLayer] = Field(min_length=1)


class IndicatorSeriesRecord(BaseModel):
    """
    One closed bar of an indicator series stream.

    Args:
        bar_time: The bar's opening instant
        timeframe: The timeframe of the bar — the series drawn on it own its values
        values: Per plot id of the series drawn on that timeframe, its value at the bar's close;
            null where the worker had none
    """
    model_config = ConfigDict(frozen=True, extra='forbid')

    bar_time: datetime
    timeframe: str
    values: Dict[str, Optional[float]]
