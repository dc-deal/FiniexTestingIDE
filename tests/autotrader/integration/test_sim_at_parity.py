"""
FiniexTestingIDE - Sim <-> Mock Session Data Parity (#438)

A mock session replays scenario base data through the SAME shared MountPreparer a backtest uses:
the same ticks, the same signal sources, and the same warmup bars — the last N bars before the
window's start. These tests hold the mock session to that. The mock path once loaded its warmup
bars itself, taking a bar file's newest bars whatever the window, so a mock session replaying
January warmed its indicators on September; the parity test below is what keeps a second path
from growing back.
"""

import pytest

from python.configuration.app_config_manager import AppConfigManager
from python.configuration.autotrader.autotrader_config_loader import load_autotrader_config
from python.framework.autotrader.autotrader_data_preparer import (
    build_scenario_from_config,
    prepare_mock_session_data,
)
from python.framework.batch.mount_preparer import MountPreparer
from python.framework.batch.requirements_collector import RequirementsCollector
from python.framework.logging.bootstrap_logger import get_global_logger
from python.framework.types.scenario_types.scenario_set_types import SingleScenario

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


def test_mock_session_warms_up_on_bars_before_its_window(prepared):
    """No warmup bar may lie at or after the replayed window's start — that would be look-ahead."""
    start = prepared.scenario.start_date
    assert prepared.package.bars, 'no warmup bars prepared — this test would check nothing'

    for (symbol, timeframe, _), bars in prepared.package.bars.items():
        assert bars, f'{symbol} {timeframe}: no warmup bars'
        late = [bar['timestamp'] for bar in bars if bar['timestamp'] >= start]
        assert not late, (
            f'{symbol} {timeframe}: {len(late)} warmup bar(s) at or after the window start '
            f'{start.isoformat()}, first {late[0]}')


def test_mock_session_records_the_price_basis_of_its_warmup_bars(prepared):
    """The consumption record measures the bar files the warmup read, as a backtest's does."""
    assert prepared.scenario.price_bases, (
        'the mock session read warmup bar files and recorded no price basis for them')
