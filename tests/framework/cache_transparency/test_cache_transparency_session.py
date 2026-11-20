"""
Cache Transparency in a Session — every position a decision reads is marked at the tick it reads on
(#653).

A session is not reproducible pass by pass: its fills and polls run on the wall clock, so two runs
with the same settings already differ in when a position appears (measured 2026-11-05). The live
side is therefore held to a property rather than to a comparison of two records: on every tick pass,
every open position the observer probe reads — through the API or through a reference it kept —
shows the price it would close at on that tick, the bid for a LONG and the ask for a SHORT. That is
what the reference mode guarantees, and what the lazy marking has to deliver too.

The sessions are mock sessions at spot with state frames on, so the frame's own marking runs between
the passes, as it does in a real session.
"""

import copy
from typing import Any, Dict, List

import pytest

from python.configuration.autotrader.autotrader_config_loader import load_autotrader_config
from python.framework.autotrader.autotrader_main import AutotraderMain
from python.framework.types.config_types.verification_config_types import VerificationConfig
from python.framework.types.portfolio_types.portfolio_types import PositionStatus
from python.framework.types.trading_env_types.order_types import OrderDirection
from tests.shared.fixture_helpers import remove_run_dir

PROFILE = 'configs/autotrader_profiles/mock/state_frames_test.json'

# An open, a partial close and a second open; the session ends with what is still open
PLAN = [
    {'trigger': 'tick', 'at': 100, 'action': 'open', 'direction': 'LONG', 'lots': 0.002},
    {'trigger': 'tick', 'at': 1500, 'action': 'partial_close', 'position_index': 0, 'lots': 0.001},
    {'trigger': 'tick', 'at': 2000, 'action': 'open', 'direction': 'LONG', 'lots': 0.002},
]


def _session_record(portfolio_cache: bool) -> List[Dict[str, Any]]:
    """One mock session traded by the observer probe; its record, with the run directory removed."""
    config = load_autotrader_config(PROFILE)
    config.strategy_config = copy.deepcopy(config.strategy_config)
    config.strategy_config['decision_logic_type'] = 'CORE/test_probes/observer_probe'
    config.strategy_config['decision_logic_config'] = {'plan': PLAN}
    config.strategy_config['worker_instances'] = {
        'probe_worker': 'CORE/test_probes/sample_probe_worker'}
    config.strategy_config['workers'] = {
        'probe_worker': {'periods': {'M5': 14, 'M30': 20}, 'bar_snapshot_checks': []}}
    config.execution.verification = VerificationConfig(portfolio_cache=portfolio_cache)
    trader = AutotraderMain(config)
    try:
        result = trader.run()
        return result.decision_statistics.probe_metadata.observations
    finally:
        remove_run_dir(trader._run_dir)


def _unmarked_reads(record: List[Dict[str, Any]]) -> List[str]:
    """Every read of an open position, on a tick pass, that does not show that tick's closing price."""
    found: List[str] = []
    for entry in record:
        if entry['kind'] != 'tick':
            continue
        quote = entry['quote']
        views = entry['reads']['get_open_positions'] + list(entry['kept'].values())
        for view in views:
            if view is None or view['status'] == PositionStatus.CLOSED.value:
                continue
            expected = quote['bid'] if view['direction'] == OrderDirection.LONG.value else quote['ask']
            if view['current_price'] != expected:
                found.append(f"pass {entry['pass']}: {view['position_id']} at "
                             f"{view['current_price']}, the tick closes it at {expected}")
    return found


@pytest.fixture(scope='module')
def records() -> Dict[str, List[Dict[str, Any]]]:
    """The probe's record in a session with the cache on and in one with it off."""
    return {'on': _session_record(True), 'off': _session_record(False)}


class TestTheSessionsAreWorthChecking:
    """A check over a session that held nothing would prove nothing."""

    def test_positions_were_held_on_tick_passes(self, records):
        held = [entry for entry in records['on']
                if entry['kind'] == 'tick' and entry['reads']['get_open_positions']]
        assert len(held) > 100, f'only {len(held)} tick passes held a position'


class TestEveryReadIsMarkedAtItsTick:
    """What a decision reads from a position is what the position is worth at that tick."""

    def test_with_the_cache_off(self, records):
        unmarked = _unmarked_reads(records['off'])
        assert not unmarked, f'{len(unmarked)} reads, the first: {unmarked[0]}'

    # The lazy marking must not reach the read: a position a fill just opened reads marked, and an
    # open one at this tick's price (#653; without the algo mask, 2026-11-05, it read 0.0)
    def test_with_the_cache_on(self, records):
        unmarked = _unmarked_reads(records['on'])
        assert not unmarked, f'{len(unmarked)} reads, the first: {unmarked[0]}'
