"""
FiniexTestingIDE - Indicator Series Types (#379)

How a worker's outputs are drawn on a run's chart: one value per bar, as an indicator pane under the
candles (RSI) or on the candles themselves (Bollinger bands). A worker declares this once, beside
its metadata, and writes no drawing code; the values are the outputs it computes anyway.

The producer states the MEANING — where the series stands, its scale, its reference lines, which
outputs belong together — and the viewer chooses the presentation: colours from the style's role,
widths, pane heights. The names follow Lightweight Charts and Pine, written in this project's form.
"""

from decimal import Decimal
from enum import IntEnum, StrEnum
from typing import List, Optional, Set, Tuple

from pydantic import BaseModel, ConfigDict, Field, model_validator

from python.framework.types.chart_types.drawing_style_types import (
    DrawingEmphasis,
    DrawingRole,
    DrawingStyle,
)

# Every field of a served model is served, a default included, so each model's serialization
# schema marks every field required — a client generating its types from it gets no optional field

# How a filled area names the two edges it lies between
PLOT_REF_PREFIX = 'plot:'
PRICE_LINE_REF_PREFIX = 'price_line:'

# A name a reader switches by — a plot id, a layer name — holds no ':', which separates the parts
# of a layer, and no '/', which is reserved for a later level of groups
_NAME_PATTERN = r'^[^:/]+$'


class IndicatorPane(StrEnum):
    """Where a declared series stands."""
    PRICE = 'price'     # on the candles' pane, on the price's own scale
    OWN = 'own'         # a pane of its own under the candles, named after the series' layer


class PriceFormatType(StrEnum):
    """How the scale's labels and the legend's digits read."""
    PRICE = 'price'
    PERCENT = 'percent'
    VOLUME = 'volume'


class PlotKind(StrEnum):
    """How one output is drawn — a Lightweight Charts series type."""
    LINE = 'line'
    HISTOGRAM = 'histogram'
    AREA = 'area'
    BASELINE = 'baseline'


class LineType(IntEnum):
    """How a line connects its points — Lightweight Charts' own codes."""
    SIMPLE = 0
    WITH_STEPS = 1
    CURVED = 2


class LineStyle(IntEnum):
    """How a line is dashed — Lightweight Charts' own codes."""
    SOLID = 0
    DOTTED = 1
    DASHED = 2
    LARGE_DASHED = 3
    SPARSE_DOTTED = 4


class PriceFormat(BaseModel):
    """
    The scale's number format.

    Args:
        type: Price, percent or volume
        precision: Decimal places shown
        min_move: The smallest step the scale shows; None leaves it to the precision
    """
    model_config = ConfigDict(frozen=True, extra='forbid',
                              json_schema_serialization_defaults_required=True)

    type: PriceFormatType = PriceFormatType.PRICE
    precision: int = Field(ge=0)
    min_move: Optional[float] = Field(default=None, gt=0)


class ValueRange(BaseModel):
    """
    A fixed scale, such as RSI's 0 to 100.

    Args:
        min_value: The scale's bottom
        max_value: The scale's top
    """
    model_config = ConfigDict(frozen=True, extra='forbid',
                              json_schema_serialization_defaults_required=True)

    min_value: float
    max_value: float

    @model_validator(mode='after')
    def _bottom_below_top(self) -> 'ValueRange':
        """
        Refuse a range whose bottom is not below its top.

        Returns:
            The range
        """
        if self.min_value >= self.max_value:
            raise ValueError(f'a range needs min_value below max_value, got '
                             f'{self.min_value} and {self.max_value}')
        return self


class PlotOptions(BaseModel):
    """
    A plot's drawing options, those that carry over to the chart library.

    Args:
        line_type: How a line connects its points
        line_style: How a line is dashed
        line_visible: False draws the points without a line
        point_markers_visible: Whether every point gets a marker
        base: A histogram's zero, which its bars grow from
        base_value: The price a baseline series is split at
    """
    model_config = ConfigDict(frozen=True, extra='forbid',
                              json_schema_serialization_defaults_required=True)

    line_type: Optional[LineType] = None
    line_style: Optional[LineStyle] = None
    line_visible: Optional[bool] = None
    point_markers_visible: Optional[bool] = None
    base: Optional[float] = None
    base_value: Optional[float] = None


class Plot(BaseModel):
    """
    One output drawn as one series.

    Args:
        id: The worker output it draws — the key into the entry's data, into filled areas and into
            the legend
        kind: Line, histogram, area or baseline
        options: Its drawing options
        style: Its role, as the theme colours it
    """
    model_config = ConfigDict(frozen=True, extra='forbid',
                              json_schema_serialization_defaults_required=True)

    id: str = Field(min_length=1, pattern=_NAME_PATTERN)
    kind: PlotKind = PlotKind.LINE
    options: PlotOptions = PlotOptions()
    style: DrawingStyle = DrawingStyle()

    def get_ref(self) -> str:
        """
        How a filled area names this plot.

        Returns:
            'plot:<id>'
        """
        return f'{PLOT_REF_PREFIX}{self.id}'


class PriceLine(BaseModel):
    """
    A horizontal reference line, such as RSI's 30 and 70.

    Args:
        price: Where it stands, on the series' scale
        line_style: How it is dashed
        title: The words beside it; None leaves it unlabelled
    """
    model_config = ConfigDict(frozen=True, extra='forbid',
                              json_schema_serialization_defaults_required=True)

    price: float
    line_style: LineStyle = LineStyle.DASHED
    title: Optional[str] = None

    def get_ref(self) -> str:
        """
        How a filled area names this line — its price as the shortest decimal that reads back as
        the same number, never in exponent form and without a trailing '.0' (70, 70.5, 0.00001234).

        Returns:
            'price_line:<price>'
        """
        return f'{PRICE_LINE_REF_PREFIX}{_short_decimal(self.price)}'


class FilledArea(BaseModel):
    """
    A filled area between two plots or between two price lines — never one of each, as Pine and
    the Charting Library refuse the mix too.

    Args:
        between: The two edges, each 'plot:<id>' or 'price_line:<price>'
        style: Its role and emphasis
    """
    model_config = ConfigDict(frozen=True, extra='forbid',
                              json_schema_serialization_defaults_required=True)

    between: Tuple[str, str]
    style: DrawingStyle = DrawingStyle(role=DrawingRole.ANNOTATION,
                                       emphasis=DrawingEmphasis.QUIET)


class IndicatorSeriesShape(BaseModel):
    """
    What a declared series and a served one share: its legend, its scale, its plots, its reference
    lines and its filled areas.

    Args:
        title: The legend's first word
        price_format: The scale's number format; None means the price's own, as the symbol's
            digits give it — a series on the candles, or one in price units (MACD)
        range: A fixed scale; None lets the scale follow the values
        plots: The outputs drawn, at least one, the first drawn behind the others
        price_lines: Reference lines
        filled_areas: Filled areas between two plots or two price lines
    """
    model_config = ConfigDict(frozen=True, extra='forbid',
                              json_schema_serialization_defaults_required=True)

    title: str = Field(min_length=1)
    price_format: Optional[PriceFormat] = None
    range: Optional[ValueRange] = None
    plots: Tuple[Plot, ...] = Field(min_length=1)
    price_lines: Tuple[PriceLine, ...] = ()
    filled_areas: Tuple[FilledArea, ...] = ()

    @model_validator(mode='after')
    def _references_resolve(self) -> 'IndicatorSeriesShape':
        """
        Refuse a repeated plot or price line, and a filled area whose edges are not two distinct
        plots or two distinct price lines of this series.

        Returns:
            The shape
        """
        plot_refs = _unique_refs([plot.get_ref() for plot in self.plots], 'plot')
        line_refs = _unique_refs([line.get_ref() for line in self.price_lines], 'price line')
        for area in self.filled_areas:
            first, second = area.between
            if first == second:
                raise ValueError(f'a filled area lies between two edges, got {first} twice')
            if not ({first, second} <= plot_refs or {first, second} <= line_refs):
                raise ValueError(
                    f'a filled area lies between two plots or two price lines of its series, '
                    f'never one of each — {first} and {second} are not '
                    f'(plots {sorted(plot_refs)}, price lines {sorted(line_refs)})')
        return self


class IndicatorSeriesDeclaration(IndicatorSeriesShape):
    """
    How a worker's outputs are drawn — declared once, by the worker class.

    Args:
        pane: On the candles, or a pane of its own
        layer_name: Names a further series of the same worker, drawn and switched apart from the
            first; None for the worker's first, whose layer is 'worker:<instance>'
        timeframe_position: Which of the worker's timeframes the series' values belong to, in the
            order its 'periods' configure them — 0, the first, unless the worker computes on more
            than one
    """
    pane: IndicatorPane
    layer_name: Optional[str] = Field(default=None, pattern=_NAME_PATTERN)
    timeframe_position: int = Field(default=0, ge=0)

    @model_validator(mode='after')
    def _fixed_scale_on_own_pane(self) -> 'IndicatorSeriesDeclaration':
        """
        Refuse a fixed range on the price pane, which keeps the price's own scale.

        Returns:
            The declaration
        """
        if self.range is not None and self.pane is IndicatorPane.PRICE:
            raise ValueError(f'{self.title}: a series on the price pane keeps the price scale — '
                             f'a fixed range needs a pane of its own')
        return self


def _short_decimal(value: float) -> str:
    """
    A number as the shortest decimal that reads back as the same float, written out in full.

    Args:
        value: The number

    Returns:
        Its digits — '70', '70.5', '0.00001234'; a negative zero reads '0'
    """
    digits = format(Decimal(repr(float(value) + 0.0)), 'f')
    return digits.rstrip('0').rstrip('.') if '.' in digits else digits


def _unique_refs(refs: List[str], what: str) -> Set[str]:
    """
    The refs of one kind, refused when one repeats.

    Args:
        refs: The refs in declaration order
        what: What they name, for the message

    Returns:
        The refs as a set
    """
    unique = set(refs)
    if len(unique) != len(refs):
        repeated = sorted({ref for ref in refs if refs.count(ref) > 1})
        raise ValueError(f'a series declares each {what} once, repeated: {repeated}')
    return unique
