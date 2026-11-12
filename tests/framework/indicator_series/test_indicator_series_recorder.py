"""
What the indicator series recorder writes (#379): one record per closed bar of a drawing worker's
timeframe, with the value the worker computed on that bar's final state — the last pass inside the
bar for a worker on the LIVE basis, the closing pass for one on the BAR_CLOSE basis — and null for a
bar it computed nothing on, never a bar left out. First against stand-ins for the run's workers and
bars, so every rule is reached in milliseconds; then on a real backtest, whose orders must not move
when the recording is switched on.
"""

import json
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, List, Optional, Tuple

import pytest

from python.configuration.app_config_manager import AppConfigManager
from python.framework.batch.batch_orchestrator import BatchOrchestrator
from python.framework.constants.run_layout_constants import RUN_TYPE_SIMULATION
from python.framework.factory.worker_factory import WorkerFactory
from python.framework.process.indicator_series_recorder import (
    IndicatorSeriesRecorder,
    open_indicator_series,
)
from python.framework.store.entry_members.run_dir_reader import RunDirReader
from python.framework.store.entry_members.run_entry_writer import RunEntryWriter
from python.framework.types.api.report_types import RunReporting
from python.framework.types.chart_types.indicator_series_entry_types import (
    IndicatorSeriesRecord,
    IndicatorSeriesStreamHeader,
)
from python.framework.types.market_types.market_data_types import Bar
from python.framework.types.store_member_types import RunEntryMemberId
from python.framework.types.worker_types import WorkerResult
from python.framework.utils.timeframe_config_utils import TimeframeConfig
from python.scenario.scenario_config_loader import ScenarioConfigLoader
from python.scenario.scenario_set import ScenarioSet
from tests.shared.fixture_helpers import remove_run_dir
from tests.shared.null_logger import NullLogger
from tests.shared.recording_logger import RecordingLogger

_SYMBOL = 'ETHUSD'
_SCENARIO = 'scenario_a'
_SERIES_SET = 'backtesting/indicator_series_test.json'


def _stamp(minute: int) -> str:
    """The opening instant of an M5 bar, as the renderer writes it."""
    return f'2026-11-03T10:{minute:02d}:00+00:00'


def _bar(minute: int, timeframe: str = 'M5') -> Bar:
    """A completed bar opening at 10:<minute>."""
    return Bar(symbol=_SYMBOL, timeframe=timeframe, timestamp=_stamp(minute), open=1.0, high=1.0,
               low=1.0, close=1.0, volume=0.0, is_complete=True)


class _Bars:
    """The bar controller as the recorder reads it: the M5 and M30 histories the test grows."""

    def __init__(self):
        self.history: List[Bar] = []
        self.higher: List[Bar] = []

    def get_bar_history(self, symbol: str, timeframe: str) -> Tuple[Bar, ...]:
        return tuple({'M5': self.history, 'M30': self.higher}.get(timeframe, ()))


class _Workers:
    """The orchestrator as the recorder reads it: its workers, and each one's newest result."""

    def __init__(self, workers: dict):
        self.workers = workers
        self.results: Dict[str, Optional[WorkerResult]] = {}

    def get_worker_result(self, name: str) -> Optional[WorkerResult]:
        return self.results.get(name)


class _DrawsNothing:
    """A worker that declares no series."""

    @classmethod
    def get_indicator_series(cls) -> tuple:
        return ()


def _rsi(compute_basis: Optional[str] = None):
    """A real RSI worker on M5, on its declared basis or the one given."""
    config = {'periods': {'M5': 14}}
    if compute_basis is not None:
        config['compute_basis'] = compute_basis
    return WorkerFactory(NullLogger()).create_worker('rsi_fast', 'CORE/rsi', config)


_TWO_TIMEFRAMES_WORKER = '''
from python.framework.types.chart_types.indicator_series_types import (
    IndicatorPane, IndicatorSeriesDeclaration, Plot,
)
from python.framework.workers.core.rsi_worker import RsiWorker


class TwoTimeframes(RsiWorker):
    @classmethod
    def get_indicator_series(cls):
        return (IndicatorSeriesDeclaration(pane=IndicatorPane.OWN, title='Lower',
                                           plots=(Plot(id='rsi_value'),)),
                IndicatorSeriesDeclaration(pane=IndicatorPane.OWN, title='Higher',
                                           layer_name='higher', timeframe_position=1,
                                           plots=(Plot(id='avg_gain'),)))
'''


def _two_timeframes(tmp_path: Path):
    """A user's worker drawing on the first and the second timeframe of its periods."""
    path = tmp_path / 'two_timeframes.py'
    path.write_text(_TWO_TIMEFRAMES_WORKER, encoding='utf-8')
    return WorkerFactory(NullLogger()).create_worker(
        'rsi_fast', str(path), {'periods': {'M5': 14, 'M30': 14}})


class _Run:
    """One recorder over stand-ins, and what it wrote."""

    def __init__(self, tmp_path: Path, workers: dict):
        self.bars = _Bars()
        self.workers = _Workers(workers)
        self.logger = RecordingLogger()
        self.recorder: Optional[IndicatorSeriesRecorder] = open_indicator_series(
            RunEntryWriter(tmp_path, RUN_TYPE_SIMULATION),
            RunEntryMemberId.SCENARIO_INDICATOR_SERIES, 'run_a', _SCENARIO, self.workers,
            self.bars, _SYMBOL, self.logger, scenario=_SCENARIO)
        self._path = tmp_path / 'io' / f'indicator_series_{_SCENARIO}' / 'rsi_fast.jsonl'

    def pass_with(self, value: Optional[float], forming_minute: int, closed: bool = False,
                  higher_minute: int = 0, higher_closed: bool = False) -> None:
        """One pass: the worker's newest value, the forming bars after it, which timeframes closed."""
        self.workers.results['rsi_fast'] = (
            WorkerResult(outputs={'rsi_value': value, 'avg_gain': value / 10})
            if value is not None else None)
        closed_timeframes = ({'M5'} if closed else set()) | ({'M30'} if higher_closed else set())
        self.recorder.on_pass(frozenset(closed_timeframes),
                              {'M5': SimpleNamespace(timestamp=_stamp(forming_minute)),
                               'M30': SimpleNamespace(timestamp=_stamp(higher_minute))})

    def lines(self) -> List[dict]:
        self.recorder.close()
        return [json.loads(line) for line in self._path.read_text().splitlines()]

    def records(self) -> List[Tuple[str, Optional[float]]]:
        return [(record['bar_time'], record.get('values', {}).get('rsi_value'))
                for record in self.lines()[1:]]


class TestTheValueThatStandsForAClosedBar:

    def test_a_live_worker_records_its_last_value_inside_the_bar(self, tmp_path):
        run = _Run(tmp_path, {'rsi_fast': _rsi()})
        run.pass_with(41.0, forming_minute=0)
        run.pass_with(42.0, forming_minute=0)
        run.bars.history.append(_bar(0))
        # The pass that closes 10:00 already computed on the first tick of 10:05
        run.pass_with(43.0, forming_minute=5, closed=True)
        assert run.records() == [(_stamp(0), 42.0)]

    def test_a_bar_close_worker_records_the_closing_pass(self, tmp_path):
        run = _Run(tmp_path, {'rsi_fast': _rsi('bar_close')})
        run.pass_with(41.0, forming_minute=0)
        run.bars.history.append(_bar(0))
        run.pass_with(43.0, forming_minute=5, closed=True)
        assert run.records() == [(_stamp(0), 43.0)]

    def test_a_pass_that_closes_no_bar_writes_nothing(self, tmp_path):
        run = _Run(tmp_path, {'rsi_fast': _rsi()})
        run.pass_with(41.0, forming_minute=0)
        run.recorder.on_pass(frozenset({'M30'}), {'M5': SimpleNamespace(timestamp=_stamp(0))})
        assert run.records() == []


class TestABarWithoutAValueIsNull:

    def test_a_bar_before_the_first_value_is_null(self, tmp_path):
        run = _Run(tmp_path, {'rsi_fast': _rsi()})
        run.pass_with(None, forming_minute=0)
        run.bars.history.append(_bar(0))
        run.pass_with(43.0, forming_minute=5, closed=True)
        assert run.records() == [(_stamp(0), None)]

    def test_a_value_that_is_no_number_yet_is_null(self, tmp_path):
        run = _Run(tmp_path, {'rsi_fast': _rsi()})
        run.pass_with(float('nan'), forming_minute=0)
        run.bars.history.append(_bar(0))
        run.pass_with(43.0, forming_minute=5, closed=True)
        assert run.records() == [(_stamp(0), None)]

    def test_bars_closed_between_two_passes_are_null_never_left_out(self, tmp_path):
        run = _Run(tmp_path, {'rsi_fast': _rsi()})
        run.pass_with(41.0, forming_minute=0)
        run.bars.history.append(_bar(0))
        run.pass_with(42.0, forming_minute=5, closed=True)
        # No pass saw 10:10 forming: its value is unknown, and the bar stays in the series
        run.bars.history.extend([_bar(5), _bar(10)])
        run.pass_with(44.0, forming_minute=15, closed=True)
        assert run.records() == [(_stamp(0), 41.0), (_stamp(5), 42.0), (_stamp(10), None)]

    def test_bars_closed_before_the_first_record_are_null_never_left_out(self, tmp_path):
        run = _Run(tmp_path, {'rsi_fast': _rsi()})
        run.pass_with(41.0, forming_minute=0)
        # The first close the recorder sees closes two bars at once
        run.bars.history.extend([_bar(0), _bar(5)])
        run.pass_with(43.0, forming_minute=10, closed=True)
        assert run.records() == [(_stamp(0), 41.0), (_stamp(5), None)]

    def test_a_warmup_bar_is_never_recorded(self, tmp_path):
        run = _Run(tmp_path, {'rsi_fast': _rsi()})
        # The warmup's newest bar, closed when the run's first tick opened the next one
        run.bars.history.append(_bar(0))
        run.pass_with(41.0, forming_minute=5, closed=True)
        run.bars.history.append(_bar(5))
        run.pass_with(42.0, forming_minute=10, closed=True)
        assert run.records() == [(_stamp(5), 41.0)]


class TestTheStreamThatIsOpened:

    def test_the_header_names_the_layer_its_pane_its_inputs_and_its_timeframe(self, tmp_path):
        run = _Run(tmp_path, {'rsi_fast': _rsi()})
        header = IndicatorSeriesStreamHeader.model_validate(run.lines()[0])
        assert (header.unit_name, header.worker, header.cadence_timeframe) == (
            _SCENARIO, 'rsi_fast', 'M5')
        layer = header.series[0]
        assert (layer.layer, layer.pane, layer.timeframe) == ('worker:rsi_fast', 'rsi_fast', 'M5')
        assert layer.inputs['period'] == 14

    def test_a_worker_on_two_timeframes_records_each_series_on_its_own_closes(self, tmp_path):
        run = _Run(tmp_path, {'rsi_fast': _two_timeframes(tmp_path)})
        run.pass_with(41.0, forming_minute=25)
        run.bars.history.append(_bar(25))
        run.bars.higher.append(_bar(0, 'M30'))
        # 10:30 closes the M5 bar of 10:25 and the M30 bar of 10:00 at once
        run.pass_with(43.0, forming_minute=30, closed=True, higher_minute=30, higher_closed=True)
        run.pass_with(44.0, forming_minute=30, higher_minute=30)
        run.bars.history.append(_bar(30))
        run.pass_with(45.0, forming_minute=35, closed=True, higher_minute=30)

        lines = run.lines()
        header = IndicatorSeriesStreamHeader.model_validate(lines[0])
        assert header.cadence_timeframe == 'M5'
        assert [(layer.layer, layer.timeframe) for layer in header.series] == [
            ('worker:rsi_fast', 'M5'), ('worker:rsi_fast:higher', 'M30')]
        assert [(line['bar_time'], line['timeframe'], line['values']) for line in lines[1:]] == [
            (_stamp(25), 'M5', {'rsi_value': 41.0}),
            (_stamp(0), 'M30', {'avg_gain': 4.1}),
            (_stamp(30), 'M5', {'rsi_value': 44.0})]

    def test_no_worker_that_draws_opens_no_stream(self, tmp_path):
        run = _Run(tmp_path, {'llm': _DrawsNothing()})
        assert run.recorder is None
        assert not (tmp_path / 'io').exists()


def _backtest(series_on: bool):
    """
    The fixture set run as a backtest that reports, its series switched on or off.

    Returns:
        (the run's summary, its run directory)
    """
    app_config = AppConfigManager()
    loaded = ScenarioConfigLoader().load_config(_SERIES_SET)
    for scenario in loaded.scenarios:
        scenario.execution_config = {**(scenario.execution_config or {}),
                                     'indicator_series': {'enabled': series_on}}
    scenario_set = ScenarioSet(loaded, app_config, reporting=RunReporting.EXPECTED)
    summary = BatchOrchestrator(scenario_set, app_config).run()
    return summary, Path(scenario_set.logger.get_log_dir())


@pytest.fixture(scope='module')
def backtests():
    """The fixture set run twice — the series recorded, and not."""
    on = _backtest(True)
    off = _backtest(False)
    yield on, off
    remove_run_dir(on[1])
    remove_run_dir(off[1])


def _bar_open(instant: datetime, step: timedelta) -> datetime:
    """The opening instant of the bar of length `step` that holds `instant`."""
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    return epoch + (instant - epoch) // step * step


def _orders(summary) -> list:
    """Every order event and trade of the run's scenarios, as plain values."""
    results = [result.tick_loop_results for result in summary.process_result_list]
    assert all(results)
    return [([asdict(event) for event in result.order_events or []],
             [asdict(trade) for trade in result.trade_history or []]) for result in results]


class TestOnARealBacktest:

    def test_the_orders_are_the_same_with_the_recording_on_and_off(self, backtests):
        (on, _), (off, _) = backtests
        orders = _orders(on)
        # The fixture trades, so the comparison holds something
        assert any(events and trades for events, trades in orders)
        assert orders == _orders(off)

    def test_the_run_without_the_recording_writes_no_series(self, backtests):
        _, (_, off_dir) = backtests
        assert not RunDirReader(off_dir).files_of(RunEntryMemberId.SCENARIO_INDICATOR_SERIES)

    def test_its_files_are_the_scenarios_never_a_sessions(self, backtests):
        # The session routes read the session's member: a backtest's series must not reach them
        (_, run_dir), _ = backtests
        reader = RunDirReader(run_dir)
        assert reader.names_of_files(RunEntryMemberId.INDICATOR_SERIES) == []
        assert {(names['scenario'], names['worker']) for names in
                reader.names_of_files(RunEntryMemberId.SCENARIO_INDICATOR_SERIES)} == {
            ('ethusd_series', 'rsi_fast'), ('ethusd_series', 'bollinger_main'),
            ('ethusd_series', 'obv_volume'), ('ethusd_series', 'tide_wave')}

    def test_each_drawing_worker_records_every_bar_that_closed_in_its_window(self, backtests):
        (summary, run_dir), _ = backtests
        ticks = summary.process_result_list[0].tick_loop_results.tick_range_stats
        streams = {}
        for path in RunDirReader(run_dir).files_of(RunEntryMemberId.SCENARIO_INDICATOR_SERIES):
            lines = [json.loads(line) for line in path.read_text().splitlines()]
            streams[IndicatorSeriesStreamHeader.model_validate(lines[0]).worker] = lines
        # Every worker of the set draws — the tide and wave worker on two timeframes
        assert set(streams) == {'rsi_fast', 'bollinger_main', 'obv_volume', 'tide_wave'}
        tide_wave = IndicatorSeriesStreamHeader.model_validate(streams['tide_wave'][0])
        assert [(layer.layer, layer.timeframe) for layer in tide_wave.series] == [
            ('worker:tide_wave', 'M5'), ('worker:tide_wave:tide', 'M30')]
        for worker, lines in streams.items():
            header = IndicatorSeriesStreamHeader.model_validate(lines[0])
            records = [IndicatorSeriesRecord.model_validate(line) for line in lines[1:]]
            for timeframe in {layer.timeframe for layer in header.series}:
                step = timedelta(minutes=TimeframeConfig.get_minutes(timeframe))
                # From the bar of the first tick to the last one a tick closed, none left out
                times = [record.bar_time for record in records if record.timeframe == timeframe]
                assert times[0] == _bar_open(ticks.first_tick_time, step), (worker, timeframe)
                assert times[-1] == _bar_open(ticks.last_tick_time, step) - step, worker
                assert all(later - earlier == step for earlier, later in zip(times, times[1:]))
            assert any(value is not None for record in records for value in record.values.values())
