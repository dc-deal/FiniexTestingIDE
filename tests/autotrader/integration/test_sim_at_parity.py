"""
FiniexTestingIDE - Sim <-> Mock Session Data Parity (#438)

A mock session replays scenario base data through the SAME shared MountPreparer a backtest uses:
the same ticks, the same signal sources, and the same warmup bars — the last N bars complete at the
window's first tick. These tests hold the mock session to that. The mock path once loaded its warmup
bars itself, taking a bar file's newest bars whatever the window, so a mock session replaying
January warmed its indicators on September; the parity test below is what keeps a second path
from growing back.
"""

import pytest

from python.configuration.app_config_manager import AppConfigManager
from python.configuration.autotrader.autotrader_config_loader import load_autotrader_config
from python.framework.autotrader.startup.autotrader_data_preparer import (
    build_scenario_from_config,
    prepare_mock_session_data,
)
from python.framework.batch.mount_preparer import MountPreparer
from python.framework.batch.requirements_collector import RequirementsCollector
from python.framework.logging.bootstrap_logger import get_global_logger
from python.framework.types.scenario_types.scenario_set_types import SingleScenario
from python.framework.utils.bar_period import is_complete_at

# A BTCUSD mock profile with a SIGNAL source and a worker that needs bars (RSI on M5).
_PROFILE = 'configs/autotrader_profiles/mock/sentiment_mock_test.json'


def _prepare_as_backtest(scenario: SingleScenario):
    """The package a backtest of this scenario runs on."""
    logger = get_global_logger()
    preparer = MountPreparer(
        logger=logger,
        app_config=AppConfigManager(),
        requirements_collector=RequirementsCollector(logger=logger),
    )
    mount = preparer.prepare_mount([scenario])
    return mount.scenario_packages[scenario.scenario_index]


@pytest.fixture(scope='module')
def prepared():
    """The mock session's prepared data — built once, because a mount load takes seconds."""
    return prepare_mock_session_data(load_autotrader_config(_PROFILE), get_global_logger())


def test_mock_session_prepares_exactly_the_backtests_data(prepared):
    """Ticks, signal sources AND warmup bars are identical to a backtest of the same window."""
    config = load_autotrader_config(_PROFILE)
    backtest = _prepare_as_backtest(build_scenario_from_config(config))
    mock = prepared.package

    assert mock.ticks == backtest.ticks, 'mock-session ticks diverge from the backtest'
    assert mock.signal_series.keys() == backtest.signal_series.keys(), (
        'mock-session signal sources diverge from the backtest')
    assert mock.bars, 'the profile needs warmup bars, and the mock package carries none'
    assert mock.bars == backtest.bars, 'mock-session warmup bars diverge from the backtest'


def test_mock_session_warms_up_on_bars_complete_at_its_first_tick(prepared):
    """
    Every warmup bar's period has ENDED by the first tick the session replays.

    The check is on the period's end, not its opening stamp: a bar opening before the start can
    still close after it, and its high, low and close then come from ticks the session has not
    seen. This test once compared the opening stamp, and on this very profile let through a bar
    that ended 219 s after the start.
    """
    package = prepared.package
    symbol = prepared.scenario.symbol
    first_tick = package.tick_ranges[symbol][0]
    assert package.bars, 'no warmup bars prepared — this test would check nothing'
    assert package.warmup_reference_time == first_tick, (
        f'warmup selected for {package.warmup_reference_time}, but the first tick is {first_tick}')

    for timeframe, bars in package.bars.items():
        assert bars, f'{symbol} {timeframe}: no warmup bars'
        late = [bar['timestamp'] for bar in bars
                if not is_complete_at(bar['timestamp'], timeframe, first_tick)]
        assert not late, (
            f'{symbol} {timeframe}: {len(late)} warmup bar(s) not complete at the first tick '
            f'{first_tick.isoformat()}, first {late[0]}')


def test_mock_session_records_the_price_basis_of_its_warmup_bars(prepared):
    """The consumption record measures the bar files the warmup read, as a backtest's does."""
    assert prepared.scenario.price_bases, (
        'the mock session read warmup bar files and recorded no price basis for them')
