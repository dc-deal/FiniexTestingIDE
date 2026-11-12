"""
FiniexTestingIDE - Indicator Series Validator (#379)

Checks a worker class's declared indicator series against the worker itself, once, when the worker
is created — a CORE worker and a user's worker file alike. What a declaration can check on its own
— a filled area between a plot and a price line, a repeated plot, a fixed range on the candles — the
types refuse while it is built; what needs the worker is checked here: every plot draws an output
the worker declares, and that output is a number — and, once the worker instance exists, every
series draws on a timeframe its configuration gives it.
"""

from typing import List, Type

from pydantic import ValidationError

from python.framework.exceptions.indicator_series_errors import IndicatorSeriesDeclarationError
from python.framework.types.chart_types.indicator_series_types import IndicatorSeriesDeclaration
from python.framework.workers.abstract_worker import AbstractWorker

# The output types a plot can draw — a value per bar on a scale
_DRAWABLE_TYPES = (int, float)


def validate_indicator_series(worker_class: Type[AbstractWorker], worker_type: str) -> None:
    """
    Refuse a worker whose declared indicator series cannot be drawn.

    Args:
        worker_class: The worker class
        worker_type: How the run names it — 'CORE/<name>' or the file path

    Returns:
        None
    """
    try:
        declarations = worker_class.get_indicator_series()
    except ValidationError as error:
        raise IndicatorSeriesDeclarationError(
            worker_type, error.errors()[0]['msg']) from error
    outputs = worker_class.get_output_schema()
    layer_names: List[str] = []
    for index, declaration in enumerate(declarations):
        _require_layer_name(worker_type, index, declaration, layer_names)
        for plot in declaration.plots:
            output = outputs.get(plot.id)
            if output is None:
                raise IndicatorSeriesDeclarationError(
                    worker_type, f"'{declaration.title}' plots '{plot.id}', which is no output of "
                                 f'the worker (its outputs: {sorted(outputs)})')
            if output.param_type is bool or output.param_type not in _DRAWABLE_TYPES:
                raise IndicatorSeriesDeclarationError(
                    worker_type, f"'{declaration.title}' plots '{plot.id}', a "
                                 f'{output.param_type.__name__} output — a plot draws a number')


def _require_layer_name(worker_type: str, index: int, declaration: IndicatorSeriesDeclaration,
                        layer_names: List[str]) -> None:
    """
    Refuse a first series that names a layer, a further one that names none, and a name twice.

    Args:
        worker_type: How the run names the worker
        index: The series' position in the declaration
        declaration: The series
        layer_names: The names the series before it took; extended with this one's

    Returns:
        None
    """
    if index == 0 and declaration.layer_name is not None:
        raise IndicatorSeriesDeclarationError(
            worker_type, f"the first series '{declaration.title}' is drawn in the worker's own "
                         f"layer and names none, got layer_name '{declaration.layer_name}'")
    if index > 0 and declaration.layer_name is None:
        raise IndicatorSeriesDeclarationError(
            worker_type, f"the further series '{declaration.title}' needs a layer_name of its own")
    if declaration.layer_name is not None:
        if declaration.layer_name in layer_names:
            raise IndicatorSeriesDeclarationError(
                worker_type, f"the layer_name '{declaration.layer_name}' is declared twice")
        layer_names.append(declaration.layer_name)


def validate_indicator_series_timeframes(worker: AbstractWorker, worker_type: str) -> None:
    """
    Refuse a worker whose series draws on a timeframe its configuration does not give it — a
    position beyond its 'periods', or a worker with no bars at all.

    Args:
        worker: The created worker
        worker_type: How the run names it — 'CORE/<name>' or the file path

    Returns:
        None
    """
    timeframes = worker.get_required_timeframes()
    for declaration in type(worker).get_indicator_series():
        if declaration.timeframe_position >= len(timeframes):
            raise IndicatorSeriesDeclarationError(
                worker_type,
                f"the series '{declaration.title}' draws on timeframe_position "
                f'{declaration.timeframe_position}, and its periods configure '
                f'{len(timeframes)} timeframe(s): {timeframes}')
