"""
The measures of the divergence report on real backtests (#512): the day attribution's equation,
and a backtest held against itself.

Two fixture scenarios run once each with state frames switched on — a spot account (ETHUSD) and a
margin account (USDJPY) — in this process. At spot, every window of the run is taken apart and the
parts add up to the change of account value exactly. At margin the attribution is not computed,
and says why. Held against itself, a run shows no divergence in any measure, in either account
model: what the measures report is what differs between the runs, never the instrument.
"""

import json
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

from python.configuration.app_config_manager import AppConfigManager
from python.framework.batch.batch_orchestrator import BatchOrchestrator
from python.framework.reporting.builders.divergence.day_attribution_builder import (
    attribute_day,
    decompose_day,
)
from python.framework.reporting.builders.divergence.equity_divergence_builder import (
    track_equity,
)
from python.framework.reporting.builders.divergence.order_divergence_builder import (
    measure_day_orders,
)
from python.framework.reporting.io.order_event_stream_io import order_event_row
from python.framework.types.api.divergence_report_types import TradingDayClassification
from python.framework.types.api.report_types import OrderEventRow, RunReporting
from python.framework.types.api.state_frame_types import StateFrame
from python.framework.types.trading_env_types.order_event_types import OrderEventType
from python.scenario.scenario_config_loader import ScenarioConfigLoader
from python.scenario.scenario_set import ScenarioSet
from tests.shared.fixture_helpers import (
    extract_process_result,
    extract_tick_loop_results,
    remove_run_dir,
)

# account model → (fixture, base currency)
_FIXTURES = {
    'spot': (Path('configs/scenario_sets/backtesting/spot_sell_test.json'), 'ETH'),
    'margin': (Path('configs/scenario_sets/backtesting/backtesting_validation_test.json'), None),
}


def _run_with_frames(tmp_path: Path, model: str) -> Tuple[List[OrderEventRow], List[StateFrame]]:
    """One fixture scenario run with state frames, its order events and frames read back."""
    fixture, _ = _FIXTURES[model]
    document = json.loads(fixture.read_text(encoding='utf-8'))
    document['scenarios'] = document['scenarios'][:1]
    document['scenario_set_name'] = f'divergence_measures_{model}'
    scenario = document['scenarios'][0]
    scenario['execution_config'] = {**scenario.get('execution_config', {}),
                                    'state_frames': {'enabled': True, 'cadence_timeframe': 'M1'}}
    path = tmp_path / f'divergence_measures_{model}.json'
    path.write_text(json.dumps(document), encoding='utf-8')
    app_config = AppConfigManager()
    scenario_set = ScenarioSet(ScenarioConfigLoader().load_config(str(path)), app_config,
                               reporting=RunReporting.NONE)
    summary = BatchOrchestrator(scenario_set, app_config).run()
    run_dir = Path(scenario_set.logger.get_log_dir())
    try:
        results = extract_tick_loop_results(extract_process_result(summary, 0))
        events = [order_event_row(event, 'my_scenario') for event in results.order_events or []]
        frame_file, = sorted((run_dir / 'io').glob('state_frames_*.jsonl'))
        lines = [json.loads(line) for line in frame_file.read_text(encoding='utf-8').splitlines()]
        frames = [StateFrame.model_validate(line) for line in lines
                  if line.get('record_kind') == 'frame']
    finally:
        remove_run_dir(run_dir)
    return events, frames


@pytest.fixture(scope='module')
def runs(tmp_path_factory) -> Dict[str, Any]:
    """Both fixtures, run once each."""
    tmp_path = tmp_path_factory.mktemp('divergence_measures')
    patcher = pytest.MonkeyPatch()
    patcher.setenv('DEBUG_MODE', '1')
    try:
        return {model: _run_with_frames(tmp_path, model) for model in _FIXTURES}
    finally:
        patcher.undo()


def _windows(frames: List[StateFrame]):
    """The whole run and its four quarters, each from a second after a frame."""
    valued = [frame for frame in frames if frame.equity is not None]
    first, last = valued[0].frame_time, valued[-1].frame_time
    span = last - first
    edges = [first + span * quarter / 4 + timedelta(seconds=1) for quarter in range(5)]
    return [(edges[0], edges[-1])] + list(zip(edges, edges[1:]))


def _day(opening, closing) -> TradingDayClassification:
    return TradingDayClassification(trading_day=opening.date(), opening=opening, closing=closing,
                                    run_id='my_run', included=True)


class TestTheRunsAreWorthMeasuring:

    @pytest.mark.parametrize('model', sorted(_FIXTURES))
    def test_each_run_filled_orders_and_valued_its_frames(self, runs, model):
        events, frames = runs[model]
        assert any(event.event_type is OrderEventType.FILLED for event in events), model
        assert frames and all(frame.equity is not None for frame in frames), model


class TestTheEquationAtSpot:

    def test_every_window_adds_up_to_its_change_of_account_value(self, runs):
        events, frames = runs['spot']
        paired_events = [('my_run', event) for event in events]
        for opening, closing in _windows(frames):
            parts, why = decompose_day(opening, closing, frames, paired_events, {}, 'ETH')
            assert why == '', (opening, closing, why)
            assert parts.residual == pytest.approx(0.0, abs=1e-6), (opening, closing, parts)

    def test_the_run_paid_fees_and_the_equation_saw_them(self, runs):
        events, frames = runs['spot']
        opening, closing = _windows(frames)[0]
        parts, _ = decompose_day(opening, closing, frames,
                                 [('my_run', event) for event in events], {}, 'ETH')
        assert parts.fees < 0


def test_a_margin_run_is_not_taken_apart_and_says_why(runs):
    events, frames = runs['margin']
    opening, closing = _windows(frames)[0]
    parts, why = decompose_day(opening, closing, frames, [('my_run', e) for e in events], {},
                               None)
    assert parts is None and 'margin account' in why


class TestARunAgainstItself:

    @pytest.mark.parametrize('model', sorted(_FIXTURES))
    def test_every_order_measures_nothing(self, runs, model):
        events, frames = runs[model]
        opening, closing = _windows(frames)[0]
        day = measure_day_orders(opening.date(), closing, [('my_run', e) for e in events],
                                 {'my_run': frames}, 'my_run', events, frames, 0.8, 0)
        assert day.orders and not day.live_one_sided and not day.backtest_one_sided
        for order in day.orders:
            assert order.lots_difference == 0
            assert order.execution_difference in (None, 0.0)
            assert order.lots_ratio in (None, 1.0) and order.equity_ratio in (None, 1.0)

    @pytest.mark.parametrize('model', sorted(_FIXTURES))
    def test_the_account_values_never_part(self, runs, model):
        _, frames = runs[model]
        opening, closing = _windows(frames)[0]
        tracking = track_equity([_day(opening, closing)], frames, frames, {}, {})
        assert tracking.shared_slots > 0
        assert tracking.largest_gap.gap == 0.0
        assert all(day.difference_pct == 0.0 for day in tracking.days)

    def test_every_part_of_the_spot_attribution_is_zero(self, runs):
        events, frames = runs['spot']
        opening, closing = _windows(frames)[0]
        own = [('my_run', event) for event in events]
        day = measure_day_orders(opening.date(), closing, own, {'my_run': frames}, 'my_run',
                                 events, frames, 0.8, 0)
        paired = {(order.live.run_id, order.live.seq): order.live for order in day.orders}
        attribution = attribute_day(opening.date(), opening, closing, frames, own, paired,
                                    frames, own, paired, 'ETH')
        assert all(value == 0.0 for value in attribution.difference.model_dump().values())
