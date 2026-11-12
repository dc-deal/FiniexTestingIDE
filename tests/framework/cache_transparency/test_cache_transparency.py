"""
Cache Transparency — nothing a decision can see changes with the portfolio's cache on or off (#653).

The portfolio marks its open positions lazily: only when something asks. That is meant to make the
tick loop faster without changing anything a strategy sees. The observer probe trades a fixed plan
and records, on every tick pass, every heartbeat pass and in every hook, everything the decision
trading API hands out — the positions it kept from earlier passes first. The same backtest runs once
with `verification.portfolio_cache` on (the ordinary behaviour) and once off (every position current
at every event); the two records must be equal, field by field, and a failure names the first pass
and field that differ.

The backtests run in this process (DEBUG_MODE), at spot and at margin, because the two account
models mark positions at different times.
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest

from python.configuration.app_config_manager import AppConfigManager
from python.framework.batch.batch_orchestrator import BatchOrchestrator
from python.framework.types.api.report_types import RunReporting
from python.scenario.scenario_config_loader import ScenarioConfigLoader
from python.scenario.scenario_set import ScenarioSet
from tests.shared.fixture_helpers import (
    extract_probe_metadata,
    extract_process_result,
    extract_tick_loop_results,
    remove_run_dir,
)

MARGIN_FIXTURE = Path('configs/scenario_sets/backtesting/backtesting_validation_test.json')
SPOT_FIXTURE = Path('configs/scenario_sets/backtesting/spot_sell_test.json')

# An open on a tick, an open on a heartbeat, a partial close, and both positions closed again
SPOT_PLAN = [
    {'trigger': 'tick', 'at': 100, 'action': 'open', 'direction': 'LONG', 'lots': 0.01},
    {'trigger': 'heartbeat', 'at': 300, 'action': 'open', 'direction': 'LONG', 'lots': 0.01},
    {'trigger': 'tick', 'at': 1500, 'action': 'partial_close', 'position_index': 0, 'lots': 0.004},
    {'trigger': 'tick', 'at': 2200, 'action': 'close', 'position_index': 1},
    {'trigger': 'tick', 'at': 2800, 'action': 'close', 'position_index': 0},
    {'trigger': 'tick', 'at': 600, 'action': 'rest_limit', 'direction': 'LONG', 'lots': 0.01,
     'offset': 0.2},
    {'trigger': 'tick', 'at': 1200, 'action': 'cancel_resting', 'resting_index': 0},
]
MARGIN_PLAN = [
    {'trigger': 'tick', 'at': 100, 'action': 'open', 'direction': 'LONG', 'lots': 0.02},
    {'trigger': 'heartbeat', 'at': 300, 'action': 'open', 'direction': 'SHORT', 'lots': 0.01},
    {'trigger': 'tick', 'at': 2500, 'action': 'partial_close', 'position_index': 0, 'lots': 0.01},
    {'trigger': 'tick', 'at': 4000, 'action': 'close', 'position_index': 1},
    {'trigger': 'tick', 'at': 5500, 'action': 'close', 'position_index': 0},
    {'trigger': 'tick', 'at': 700, 'action': 'rest_limit', 'direction': 'SHORT', 'lots': 0.01,
     'offset': 0.2},
    {'trigger': 'tick', 'at': 1800, 'action': 'cancel_resting', 'resting_index': 0},
]
MARGIN_MAX_TICKS = 6000


def _variant(tmp_path: Path, fixture: Path, name: str, plan: List[Dict[str, Any]],
             portfolio_cache: bool, max_ticks: Optional[int] = None) -> str:
    """A fixture set's first scenario, traded by the observer probe, written under a new name."""
    document = json.loads(fixture.read_text(encoding='utf-8'))
    document['scenarios'] = document['scenarios'][:1]
    document['scenario_set_name'] = f'cache_transparency_{name}'
    strategy = document['global']['strategy_config']
    strategy['decision_logic_type'] = 'CORE/test_probes/observer_probe'
    strategy['decision_logic_config'] = {'plan': plan}
    strategy['workers']['probe_worker']['bar_snapshot_checks'] = []
    scenario = document['scenarios'][0]
    if max_ticks is not None:
        scenario['max_ticks'] = max_ticks
    scenario['execution_config'] = {**scenario.get('execution_config', {}),
                                    'verification': {'portfolio_cache': portfolio_cache}}
    path = tmp_path / f'cache_transparency_{name}.json'
    path.write_text(json.dumps(document), encoding='utf-8')
    return str(path)


def _observations(set_path: str) -> Tuple[List[Dict[str, Any]], Path]:
    """One backtest of a set: the probe's record, and the run directory to remove."""
    app_config = AppConfigManager()
    scenario_set = ScenarioSet(ScenarioConfigLoader().load_config(set_path), app_config,
                               reporting=RunReporting.NONE)
    summary = BatchOrchestrator(scenario_set, app_config).run()
    metadata = extract_probe_metadata(extract_tick_loop_results(extract_process_result(summary, 0)))
    return metadata.observations, Path(scenario_set.logger.get_log_dir())


def _first_difference(on: Any, off: Any, path: str = '') -> Optional[str]:
    """The path of the first leaf where two plain records differ, or None."""
    if isinstance(on, dict) and isinstance(off, dict):
        for key in list(on) + [key for key in off if key not in on]:
            if key not in on or key not in off:
                return f'{path}.{key} (present on one side only)'
            found = _first_difference(on[key], off[key], f'{path}.{key}')
            if found:
                return found
        return None
    if isinstance(on, list) and isinstance(off, list):
        for index, (left, right) in enumerate(zip(on, off)):
            found = _first_difference(left, right, f'{path}[{index}]')
            if found:
                return found
        if len(on) != len(off):
            return f'{path} (length {len(on)} against {len(off)})'
        return None
    if on != off:
        return f'{path}: cache on {on!r} · cache off {off!r}'
    return None


def _records_pair(tmp_path_factory, fixture: Path, plan: List[Dict[str, Any]], name: str,
                  max_ticks: Optional[int] = None) -> Dict[str, Any]:
    """The probe's record with the cache on and off, for one fixture."""
    tmp_path = tmp_path_factory.mktemp(f'cache_transparency_{name}')
    patcher = pytest.MonkeyPatch()
    patcher.setenv('DEBUG_MODE', '1')
    run_dirs: List[Path] = []
    try:
        on, run_dir = _observations(_variant(tmp_path, fixture, f'{name}_on', plan, True, max_ticks))
        run_dirs.append(run_dir)
        off, run_dir = _observations(_variant(tmp_path, fixture, f'{name}_off', plan, False, max_ticks))
        run_dirs.append(run_dir)
    finally:
        patcher.undo()
    return {'on': on, 'off': off, 'run_dirs': run_dirs}


@pytest.fixture(scope='module')
def spot(tmp_path_factory):
    """The spot fixture's two records."""
    pair = _records_pair(tmp_path_factory, SPOT_FIXTURE, SPOT_PLAN, 'spot')
    yield pair
    for run_dir in pair['run_dirs']:
        remove_run_dir(run_dir)


@pytest.fixture(scope='module')
def margin(tmp_path_factory):
    """The margin fixture's two records."""
    pair = _records_pair(tmp_path_factory, MARGIN_FIXTURE, MARGIN_PLAN, 'margin', MARGIN_MAX_TICKS)
    yield pair
    for run_dir in pair['run_dirs']:
        remove_run_dir(run_dir)


def _assert_worth_comparing(record: List[Dict[str, Any]]) -> None:
    """The plan opened a position on a tick and one on a heartbeat, and held and closed them."""
    kinds = [entry['kind'] for entry in record]
    assert kinds.count('order_filled') == 2, f'the plan opened {kinds.count("order_filled")} positions, not 2'
    assert kinds.count('partial_close') == 1, 'the plan closed no position in part'
    assert kinds.count('position_closed') == 2, f'the plan closed {kinds.count("position_closed")} positions, not 2'
    heartbeats_holding = [entry for entry in record
                          if entry['kind'] == 'heartbeat' and entry['reads']['get_open_positions']]
    assert heartbeats_holding, 'no heartbeat pass saw an open position — the fixture has no gap to test'
    assert any(len(entry['kept']) >= 2 for entry in record), 'the probe never held two positions'
    assert any(entry['reads']['get_active_orders'] for entry in record), 'no pass saw a resting order'
    assert any(entry['kept_orders'] for entry in record), 'the probe never kept a resting order'
    assert kinds.count('order_cancelled') == 1, 'the plan cancelled no resting order'
    results = [entry['event']['result'] for entry in record
               if entry.get('event') and 'result' in entry['event']]
    assert results and all('order_id' in result and 'status' in result for result in results), \
        'the events carry no readable result'


def _assert_equal(pair: Dict[str, Any]) -> None:
    """The two records are equal; otherwise the first differing pass and field."""
    on, off = pair['on'], pair['off']
    for index, (left, right) in enumerate(zip(on, off)):
        found = _first_difference(left, right)
        if found:
            pytest.fail(f'pass {left["pass"]} ({left["kind"]}): {found}')
    assert len(on) == len(off), f'{len(on)} passes with the cache on, {len(off)} with it off'


class TestTheFixturesAreWorthComparing:
    """A comparison over a run that held nothing would prove nothing."""

    def test_spot(self, spot):
        _assert_worth_comparing(spot['on'])

    def test_margin(self, margin):
        _assert_worth_comparing(margin['on'])


class TestNothingADecisionSeesDependsOnTheCache:
    """Every pass, every field: the same with the portfolio's cache on and off."""

    # At spot the engine marks a position only past an extreme; what a decision reads must still be
    # the price now, and a position a fill just opened must read marked in its own hook (#653).
    # Measured without the algo mask, 2026-11-05: about 2,945 of the fixture's 3,000 passes differed
    def test_at_spot(self, spot):
        _assert_equal(spot)

    # At margin every position is marked before the decision; a heartbeat pass must also see the
    # swap booked up to its clock (#653). Measured without the algo mask, 2026-11-05: about 1,834
    # heartbeat passes differed in the swap's accrual stamp
    def test_at_margin(self, margin):
        _assert_equal(margin)
