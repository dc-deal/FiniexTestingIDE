"""
FiniexTestingIDE - Sim <-> AutoTrader-mock Data Parity (#438)

The AutoTrader-mock replays scenario base data through the SAME shared MountPreparer the sim uses.
This asserts the prepared ticks (and signal sources) are identical for the same scenario window —
the mock is truly "a scenario replayed through the live decision path". The only difference is
`include_warmup_bars`: the AT skips bar preparation (it loads warmup bars itself), the sim loads it.
"""

from python.configuration.app_config_manager import AppConfigManager
from python.framework.batch.mount_preparer import MountPreparer
from python.framework.batch.requirements_collector import RequirementsCollector
from python.framework.logging.bootstrap_logger import get_global_logger
from python.framework.types.scenario_types.scenario_set_types import SingleScenario
from python.framework.utils.time_utils import parse_datetime


def _scenario(name: str) -> SingleScenario:
    """A BTCUSD scenario with warmup lead inside the crypto_sentiment archive window."""
    return SingleScenario(
        name=name,
        scenario_index=0,
        symbol='BTCUSD',
        data_broker_type='kraken_spot',
        data_sentiment_type='crypto_sentiment',
        start_date=parse_datetime('2026-04-27T10:00:00+00:00'),
        max_ticks=5000,
        strategy_config={
            'decision_logic_type': 'CORE/hybrid_sentiment_reference',
            'worker_instances': {
                'rsi_fast': 'CORE/rsi',
                'sentiment': 'CORE/llm_sentiment',
            },
            'workers': {
                'rsi_fast': {'periods': {'M5': 14}},
                'sentiment': {'max_staleness_minutes': 30},
            },
            'decision_logic_config': {'lot_size': 0.001},
        },
        trade_simulator_config={'balances': {'USD': 10000.0, 'BTC': 0.0}},
    )


def _prepare(scenario: SingleScenario, include_warmup_bars: bool):
    logger = get_global_logger()
    preparer = MountPreparer(
        logger=logger,
        app_config=AppConfigManager(),
        requirements_collector=RequirementsCollector(logger=logger),
    )
    mount = preparer.prepare_mount([scenario], include_warmup_bars=include_warmup_bars)
    return mount.scenario_packages[scenario.scenario_index]


def test_at_mock_and_sim_share_the_same_ticks():
    """The AT-mock (include_warmup_bars=False) and the sim (True) resolve identical ticks + signal sources."""
    pkg_sim = _prepare(_scenario('sim'), include_warmup_bars=True)
    pkg_at = _prepare(_scenario('at'), include_warmup_bars=False)

    assert pkg_at.ticks == pkg_sim.ticks, (
        'AutoTrader-mock ticks diverge from the sim for the same scenario window'
    )
    assert pkg_at.signal_series.keys() == pkg_sim.signal_series.keys(), (
        'AutoTrader-mock signal sources diverge from the sim'
    )


def test_at_mock_skips_warmup_bars_the_sim_loads():
    """The flag is the only difference: the AT skips bar preparation, the sim loads warmup bars."""
    pkg_sim = _prepare(_scenario('sim2'), include_warmup_bars=True)
    pkg_at = _prepare(_scenario('at2'), include_warmup_bars=False)

    assert pkg_at.bars == {}, 'AutoTrader-mock package must carry no warmup bars'
    assert pkg_sim.bars, 'sim package must carry warmup bars'
