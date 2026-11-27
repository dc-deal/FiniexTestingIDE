"""
FiniexTestingIDE - Indicator Series Recorder (#379)

Records each drawing worker's outputs once per closed bar, so a run's chart can show them as
indicator panes. A series is drawn on the timeframe it declares — the worker's first unless it
computes on more than one — and each timeframe a worker draws on keeps its own bars. One stream per
worker instance, each record naming its timeframe; one class for both pipelines, called
by each runner right beside its state frame, after the pass and before the end check — it reads the
timeframes the pass saw close and the outputs the workers already computed, and changes neither.

Which value stands for a closed bar: the one the worker computed on that bar's final state. A worker
on the LIVE basis computes on the forming bar, so on the pass that closes a bar its newest output
already holds a tick of the next one — its value for the closed bar is the one from its last pass
inside that bar, which the recorder keeps from pass to pass. A worker on the BAR_CLOSE basis
computes on the pass that closes the bar, on the completed bars, so that pass's output is the
value. A bar the worker computed nothing on — its warmup, a stretch the clipping skipped — records
null, and is never left out.
"""

import math
from dataclasses import dataclass
from enum import Enum
from numbers import Real
from typing import Any, Dict, FrozenSet, List, Mapping, Optional, Tuple

from python.framework.bars.bar_rendering_controller import BarRenderingController
from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.reporting.io.run_stream_appender import RunStreamAppender
from python.framework.store.entry_members.run_entry_writer import RunEntryWriter
from python.framework.types.chart_types.indicator_series_entry_types import (
    IndicatorInput,
    IndicatorSeriesLayer,
)
from python.framework.types.chart_types.indicator_series_types import (
    IndicatorPane,
    IndicatorSeriesDeclaration,
)
from python.framework.types.market_types.market_data_types import Bar, FormingBar
from python.framework.types.store_member_types import RunEntryMemberId
from python.framework.types.worker_types import ComputeBasis, WorkerResult
from python.framework.workers.abstract_indicator_worker import AbstractIndicatorWorker
from python.framework.workers.abstract_worker import AbstractWorker
from python.framework.utils.timeframe_config_utils import TimeframeConfig
from python.framework.workers.worker_orchestrator import WorkerOrchestrator

# The parameter of an indicator worker that names its timeframes, shown as its period
_PERIODS_KEY = 'periods'


@dataclass
class _DrawnTimeframe:
    """
    One timeframe a drawing worker draws on, with what the recorder keeps between passes.

    Args:
        timeframe: The timeframe whose closed bars give a record
        plot_ids: The plot ids of the series drawn on it
        first_bar_time: The opening instant of the bar forming on the run's first pass — the first
            bar the run records; the bars before it are the warmup's
        last_bar_time: The opening instant of the newest bar recorded
        previous_bar_time: The opening instant of the bar the previous pass computed on
    """
    timeframe: str
    plot_ids: Tuple[str, ...]
    first_bar_time: Optional[str] = None
    last_bar_time: Optional[str] = None
    previous_bar_time: Optional[str] = None


@dataclass
class _DrawnWorker:
    """
    One drawing worker instance and its stream.

    Args:
        name: The worker instance
        live: Whether it computes on the forming bar
        appender: Its stream
        timeframes: The timeframes its series are drawn on, each with its own bars
        previous_result: Its result on the previous pass — a LIVE worker's value for a bar that closes
    """
    name: str
    live: bool
    appender: RunStreamAppender
    timeframes: List[_DrawnTimeframe]
    previous_result: Optional[WorkerResult] = None


class IndicatorSeriesRecorder:
    """Writes each drawing worker's value per closed bar of each timeframe it draws on."""

    def __init__(self, drawn: List[_DrawnWorker], worker_orchestrator: WorkerOrchestrator,
                 bar_controller: BarRenderingController, symbol: str):
        """
        Hold the streams the opener opened.

        Args:
            drawn: The drawing workers, each with its stream
            worker_orchestrator: The run's workers, read for their results
            bar_controller: The run's bars, read for the bars that closed
            symbol: The traded symbol
        """
        self._drawn = drawn
        self._worker_orchestrator = worker_orchestrator
        self._bar_controller = bar_controller
        self._symbol = symbol

    def on_pass(self, closed_timeframes: FrozenSet[str],
                current_bars: Mapping[str, FormingBar]) -> None:
        """
        After a pass: record each timeframe of each drawing worker that closed a bar, then keep
        what a LIVE worker computed on, for the bars that close next.

        Args:
            closed_timeframes: The timeframes whose bar closed since the previous pass
            current_bars: The forming bars after the pass

        Returns:
            None
        """
        for drawn in self._drawn:
            for drawn_timeframe in drawn.timeframes:
                if drawn_timeframe.first_bar_time is None:
                    first = current_bars.get(drawn_timeframe.timeframe)
                    drawn_timeframe.first_bar_time = first.timestamp if first is not None else None
                if drawn_timeframe.timeframe in closed_timeframes:
                    self._record_closes(drawn, drawn_timeframe)
            if drawn.live:
                drawn.previous_result = self._worker_orchestrator.get_worker_result(drawn.name)
                for drawn_timeframe in drawn.timeframes:
                    forming = current_bars.get(drawn_timeframe.timeframe)
                    drawn_timeframe.previous_bar_time = (
                        forming.timestamp if forming is not None else None)

    def close(self) -> None:
        """
        Close every stream.

        Returns:
            None
        """
        for drawn in self._drawn:
            drawn.appender.close()

    def _record_closes(self, drawn: _DrawnWorker, drawn_timeframe: _DrawnTimeframe) -> None:
        """
        Write a record for every bar of one timeframe that closed since the newest one recorded
        — normally the one bar that just closed. Before the first record, every closed bar from
        the one forming on the run's first pass, so a stretch the first passes skipped is null
        rather than left out, and no warmup bar is recorded.

        Args:
            drawn: The drawing worker
            drawn_timeframe: The timeframe that closed a bar

        Returns:
            None
        """
        history = self._bar_controller.get_bar_history(self._symbol, drawn_timeframe.timeframe)
        if not history:
            return
        newest = history[-1]
        last = drawn_timeframe.last_bar_time
        first = drawn_timeframe.first_bar_time
        closed: List[Bar] = [newest]
        if last is not None or first is not None:
            closed = []
            for bar in reversed(history):
                if last is not None and bar.timestamp <= last:
                    break
                if last is None and bar.timestamp < first:
                    break
                closed.append(bar)
            closed.reverse()
        for bar in closed:
            result = self._result_for(drawn, drawn_timeframe, bar, bar is newest)
            values = {plot_id: (_number(result.outputs.get(plot_id)) if result is not None
                                else None)
                      for plot_id in drawn_timeframe.plot_ids}
            drawn.appender.write(
                {'bar_time': bar.timestamp, 'timeframe': drawn_timeframe.timeframe,
                 'values': values},
                at=f'{drawn.name} {drawn_timeframe.timeframe} {bar.timestamp}')
            drawn_timeframe.last_bar_time = bar.timestamp

    def _result_for(self, drawn: _DrawnWorker, drawn_timeframe: _DrawnTimeframe, bar: Bar,
                    newest: bool) -> Optional[WorkerResult]:
        """
        The worker's result that stands for a closed bar.

        Args:
            drawn: The drawing worker
            drawn_timeframe: The timeframe of the bar
            bar: The closed bar
            newest: Whether it is the bar that closed on this pass

        Returns:
            The result, or None when the worker computed nothing on that bar
        """
        if drawn.live:
            return (drawn.previous_result
                    if drawn_timeframe.previous_bar_time == bar.timestamp else None)
        return self._worker_orchestrator.get_worker_result(drawn.name) if newest else None


def open_indicator_series(
    run_writer: RunEntryWriter,
    member_id: RunEntryMemberId,
    run_id: str,
    unit_name: str,
    worker_orchestrator: WorkerOrchestrator,
    bar_controller: BarRenderingController,
    symbol: str,
    logger: AbstractLogger,
    **names: str,
) -> Optional[IndicatorSeriesRecorder]:
    """
    Open one indicator series stream per drawing worker instance. The worker factory has checked
    that every series draws on a timeframe the worker's configuration gives it.

    Args:
        run_writer: The run's writer
        member_id: The stream member — a session's, or a backtest scenario's
        run_id: The run the streams belong to
        unit_name: The session or the scenario
        worker_orchestrator: The run's workers
        bar_controller: The run's bars
        symbol: The traded symbol
        logger: The channel a failed write is said on
        names: The member's further name parts — a backtest's scenario

    Returns:
        The recorder, or None when no worker draws
    """
    drawn: List[_DrawnWorker] = []
    for name, worker in worker_orchestrator.workers.items():
        declarations = type(worker).get_indicator_series()
        if not declarations:
            continue
        timeframes = worker.get_required_timeframes()
        layers: List[IndicatorSeriesLayer] = []
        plot_ids: Dict[str, List[str]] = {}
        for declaration in declarations:
            timeframe = timeframes[declaration.timeframe_position]
            layer = _layer_of(declaration, name, _inputs_of(worker, timeframe), timeframe)
            layers.append(layer)
            plot_ids.setdefault(timeframe, []).extend(plot.id for plot in layer.plots)
        cadence = min(plot_ids, key=TimeframeConfig.get_minutes)
        appender = run_writer.open_stream(
            member_id, run_id, logger,
            stops=f"The indicator series of worker '{name}' is no longer recorded",
            header_fields={'unit_name': unit_name, 'worker': name, 'cadence_timeframe': cadence,
                           'series': [layer.model_dump(mode='json') for layer in layers]},
            worker=name, **names)
        live = (not isinstance(worker, AbstractIndicatorWorker)
                or worker.get_compute_basis() == ComputeBasis.LIVE)
        drawn.append(_DrawnWorker(
            name=name, live=live, appender=appender,
            timeframes=[_DrawnTimeframe(timeframe=timeframe, plot_ids=tuple(ids))
                        for timeframe, ids in plot_ids.items()]))
    if not drawn:
        return None
    described = ', '.join(
        f"{d.name} ({', '.join(t.timeframe for t in d.timeframes)})" for d in drawn)
    logger.info(f'📈 Indicator series: one value per closed bar for {described}')
    return IndicatorSeriesRecorder(drawn, worker_orchestrator, bar_controller, symbol)


def _layer_of(declaration: IndicatorSeriesDeclaration, worker_name: str,
              inputs: Dict[str, IndicatorInput], timeframe: str) -> IndicatorSeriesLayer:
    """
    A declared series in its instance's layer and pane.

    Args:
        declaration: The series as the worker class declares it
        worker_name: The worker instance
        inputs: The legend's inputs
        timeframe: The timeframe its values belong to

    Returns:
        The series as the run draws it
    """
    own = worker_name if declaration.layer_name is None \
        else f'{worker_name}:{declaration.layer_name}'
    shape = declaration.model_dump(exclude={'pane', 'layer_name', 'timeframe_position'})
    return IndicatorSeriesLayer(
        **shape, layer=f'worker:{own}',
        pane=IndicatorPane.PRICE.value if declaration.pane is IndicatorPane.PRICE else own,
        inputs=inputs, timeframe=timeframe)


def _inputs_of(worker: AbstractWorker, timeframe: str) -> Dict[str, IndicatorInput]:
    """
    The legend's inputs: the worker's period on its timeframe, then each plain parameter it
    declares, in the order its schema declares them.

    Args:
        worker: The worker instance
        timeframe: Its timeframe

    Returns:
        The inputs by name
    """
    inputs: Dict[str, IndicatorInput] = {}
    periods = worker.parameters.get(_PERIODS_KEY)
    if isinstance(periods, dict) and isinstance(periods.get(timeframe), int):
        inputs['period'] = periods[timeframe]
    for key in type(worker).get_parameter_schema():
        value = worker.parameters.get(key)
        if isinstance(value, Enum):
            value = value.value
        if isinstance(value, (bool, int, float, str)):
            inputs[key] = value
    return inputs


def _number(value: Any) -> Optional[float]:
    """
    An output as a plotted value — None where it is no number, or not one yet.

    Args:
        value: The output

    Returns:
        The value, or None
    """
    if isinstance(value, bool) or not isinstance(value, Real):
        return None
    number = float(value)
    return None if math.isnan(number) or math.isinf(number) else number
