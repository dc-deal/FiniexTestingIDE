"""
Inputs of Record, Event by Event — the backtest of a session's window changes nothing it computes (#512).

The divergence report backtests a session's window with what the session recorded: its warmup
history and its frozen broker configuration, and with state frames switched on (#579 part B item 3).
None of the three may change what the backtest computes from the same inputs, or the comparison would
measure the instrument rather than the parity. So the same scenario runs four times — the archive's
warmup and the broker file with frames off, then frames on, then the archive's own warmup fed back as
a record, then the broker file frozen and read back as a document — and every run must give the
same order events and the same trades, event by event, and the same bars where the probe looked.

The scenario runs in this process (DEBUG_MODE), which lets the test capture the warmup history the
archive path selected, the way a session records its own.
"""

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

from python.configuration.app_config_manager import AppConfigManager
from python.configuration.market_config_manager import MarketConfigManager
from python.framework.bars.warmup_history import WarmupHistory
from python.framework.batch.batch_orchestrator import BatchOrchestrator
from python.framework.factory.broker_config_factory import BrokerConfigFactory
from python.framework.store.broker_config_freeze import freeze_broker_config
from python.framework.types.api.report_types import RunReporting
from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.types.market_types.warmup_history_types import (
    WarmupHistoryRecord,
    WarmupSource,
)
from python.scenario.scenario_config_loader import ScenarioConfigLoader
from python.scenario.scenario_set import ScenarioSet
from tests.shared.fixture_helpers import (
    extract_probe_metadata,
    extract_process_result,
    extract_tick_loop_results,
    remove_run_dir,
)

FIXTURE = Path('configs/scenario_sets/backtesting/backtesting_validation_test.json')
# A spot account: its positions are not re-marked on every tick, so a frame that re-marked one
# would change what the decision reads next — the case the margin fixture cannot show
SPOT_FIXTURE = Path('configs/scenario_sets/backtesting/spot_sell_test.json')


def _variant(tmp_path: Path, name: str, scenario_keys: Dict[str, Any],
             execution: Dict[str, Any], fixture: Path = FIXTURE) -> str:
    """A fixture set, written under a new name with keys added to its first scenario."""
    document = json.loads(fixture.read_text(encoding='utf-8'))
    document['scenarios'] = document['scenarios'][:1]
    document['scenario_set_name'] = f'inputs_of_record_{name}'
    scenario = document['scenarios'][0]
    scenario.update(scenario_keys)
    scenario['execution_config'] = {**scenario.get('execution_config', {}), **execution}
    path = tmp_path / f'inputs_of_record_{name}.json'
    path.write_text(json.dumps(document), encoding='utf-8')
    return str(path)


def _run(set_path: str):
    """One backtest of a set, with the run directory its scenario wrote into."""
    app_config = AppConfigManager()
    scenario_set = ScenarioSet(ScenarioConfigLoader().load_config(set_path), app_config,
                               reporting=RunReporting.NONE)
    summary = BatchOrchestrator(scenario_set, app_config).run()
    return summary, Path(scenario_set.logger.get_log_dir())


def _events(summary: BatchExecutionSummary) -> List[Dict[str, Any]]:
    """The scenario's order events as plain dicts — the stream the report compares."""
    results = extract_tick_loop_results(extract_process_result(summary, 0))
    return [event.model_dump(mode='json') if hasattr(event, 'model_dump') else asdict(event)
            for event in results.order_events or []]


def _trades(summary: BatchExecutionSummary) -> List[Dict[str, Any]]:
    """The scenario's closed trades as plain dicts."""
    results = extract_tick_loop_results(extract_process_result(summary, 0))
    return [asdict(trade) for trade in results.trade_history]


@pytest.fixture(scope='module')
def runs(tmp_path_factory) -> Dict[str, Any]:
    """The four runs of one scenario, and the archive warmup the first one selected."""
    tmp_path = tmp_path_factory.mktemp('inputs_of_record')
    patcher = pytest.MonkeyPatch()
    patcher.setenv('DEBUG_MODE', '1')
    captured: List[WarmupHistoryRecord] = []
    original = WarmupHistory.from_package

    def capture(symbol, package, logger, required_by_timeframe=None):
        history = original(symbol, package, logger, required_by_timeframe=required_by_timeframe)
        captured.append(history.build_record())
        return history

    patcher.setattr(WarmupHistory, 'from_package', staticmethod(capture))
    run_dirs: List[Path] = []
    try:
        baseline, run_dir = _run(_variant(tmp_path, 'baseline', {}, {}))
        run_dirs.append(run_dir)
        assert captured, 'the archive path selected no warmup history'
        archive_record = captured[0]

        frames, frames_dir = _run(_variant(
            tmp_path, 'frames', {}, {'state_frames': {'enabled': True, 'cadence_timeframe': 'M1'}}))
        run_dirs.append(frames_dir)

        patcher.setattr('python.scenario.scenario_config_loader.read_warmup_record',
                        lambda run_id, reader=None: archive_record)
        recorded_warmup, run_dir = _run(_variant(
            tmp_path, 'warmup_record', {'warmup_from_run': 'archive_selection_run'}, {}))
        run_dirs.append(run_dir)

        seed = BrokerConfigFactory.build_broker_config(
            MarketConfigManager().get_broker_config_path('mt5'))
        freeze_broker_config(seed, MagicMock())
        document, run_dir = _run(_variant(
            tmp_path, 'broker_document',
            {'broker_config_document': seed.get_frozen_config_id()}, {}))
        run_dirs.append(run_dir)
        frame_lines = _frame_lines(frames_dir)
    finally:
        patcher.undo()
    yield {'baseline': baseline, 'frames': frames, 'warmup_record': recorded_warmup,
           'broker_document': document, 'archive_record': archive_record,
           'frame_lines': frame_lines}
    for run_dir in run_dirs:
        remove_run_dir(run_dir)


def _frame_lines(run_dir: Path) -> List[Dict[str, Any]]:
    """The frames the scenario wrote, its stream's header left out."""
    paths = sorted((run_dir / 'io').glob('state_frames_*.jsonl'))
    assert len(paths) == 1, f'expected one scenario frame stream in {run_dir / "io"}, found {paths}'
    lines = [json.loads(line) for line in paths[0].read_text(encoding='utf-8').splitlines()]
    return lines[1:]


class TestTheBaselineIsWorthComparing:
    """A comparison over a run that placed nothing would prove nothing."""

    def test_the_baseline_placed_orders_and_traded(self, runs):
        assert _events(runs['baseline']), 'the fixture placed no order'
        assert _trades(runs['baseline']), 'the fixture closed no trade'


class TestFramesChangeNothing:
    """Recording state frames reads the run; it never changes what the run computes."""

    def test_the_frames_were_written(self, runs):
        frames = runs['frame_lines']
        assert frames, 'the run with frames switched on wrote none'
        causes = {frame['written_for'] for frame in frames}
        assert {'cadence', 'order', 'shutdown'} <= causes, f'causes written: {sorted(causes)}'
        # A line leaves out what is None, so a frame without a reading carries no such key
        assert all(frame.get('process_memory') is None for frame in frames), 'a backtest reads no memory'

    def test_the_same_order_events(self, runs):
        assert _events(runs['frames']) == _events(runs['baseline'])

    def test_the_same_trades(self, runs):
        assert _trades(runs['frames']) == _trades(runs['baseline'])


@pytest.fixture(scope='module')
def spot_runs(tmp_path_factory) -> Dict[str, Any]:
    """One spot scenario with frames off and on."""
    tmp_path = tmp_path_factory.mktemp('inputs_of_record_spot')
    patcher = pytest.MonkeyPatch()
    patcher.setenv('DEBUG_MODE', '1')
    run_dirs: List[Path] = []
    try:
        baseline, run_dir = _run(_variant(tmp_path, 'spot_baseline', {}, {}, SPOT_FIXTURE))
        run_dirs.append(run_dir)
        frames, frames_dir = _run(_variant(
            tmp_path, 'spot_frames', {}, {'state_frames': {'enabled': True, 'cadence_timeframe': 'M1'}},
            SPOT_FIXTURE))
        run_dirs.append(frames_dir)
        frame_lines = _frame_lines(frames_dir)
    finally:
        patcher.undo()
    yield {'baseline': baseline, 'frames': frames, 'frame_lines': frame_lines}
    for run_dir in run_dirs:
        remove_run_dir(run_dir)


class TestFramesChangeNothingAtSpot:
    """At spot a re-mark would reach the decision; a backtest's frame makes none."""

    def test_the_baseline_traded(self, spot_runs):
        assert _events(spot_runs['baseline']), 'the spot fixture placed no order'

    def test_the_frames_were_written(self, spot_runs):
        assert spot_runs['frame_lines'], 'the spot run with frames on wrote none'

    def test_the_same_order_events(self, spot_runs):
        assert _events(spot_runs['frames']) == _events(spot_runs['baseline'])

    def test_the_same_trades_excursions_included(self, spot_runs):
        assert _trades(spot_runs['frames']) == _trades(spot_runs['baseline'])


class TestTheRecordedWarmupIsTheArchiveWarmup:
    """The archive's own selection, fed back as a record, reproduces the archive path."""

    def test_the_record_came_from_the_archive(self, runs):
        assert runs['archive_record'].source is WarmupSource.ARCHIVE

    def test_the_same_order_events(self, runs):
        assert _events(runs['warmup_record']) == _events(runs['baseline'])

    def test_the_same_trades(self, runs):
        assert _trades(runs['warmup_record']) == _trades(runs['baseline'])

    def test_the_probe_saw_the_same_bars(self, runs):
        def snapshots(summary):
            return extract_probe_metadata(extract_tick_loop_results(extract_process_result(summary, 0)))
        assert snapshots(runs['warmup_record']) == snapshots(runs['baseline'])


class TestTheBrokerDocumentIsTheBrokerFile:
    """The broker file, frozen and read back as a document, trades exactly as the file does."""

    def test_the_same_order_events(self, runs):
        assert _events(runs['broker_document']) == _events(runs['baseline'])

    def test_the_same_trades(self, runs):
        assert _trades(runs['broker_document']) == _trades(runs['baseline'])
