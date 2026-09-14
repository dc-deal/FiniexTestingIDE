"""
The two places that answer "how many bars does this worker need" must agree.

An indicator worker states its warmup twice, for two different readers:

    calculate_requirements(config)   CLASSMETHOD — the batch pipeline calls it before any
                                     worker exists, to size the bar LOAD
    get_warmup_requirements()        instance — checked against the loaded history at
                                     runtime, and what raises INSUFFICIENT WARMUP BARS

Nothing connected them. Measured 2026-09-14: the instance method was moved to the real
warmup of a recursive average while the classmethod still returned the bare period, so the
pipeline loaded 50 bars and the runtime demanded 150. The run reported
`Insufficient bars: H1: 50/150` and failed — correctly, but for a reason that looked like a
data problem and is not one. The data preparation was right the whole time: it takes
`.tail(req.warmup_count)` and honoured exactly the number it was handed.

That is why the existing warmup coverage could not catch it. Those tests check that the
pipeline HONOURS a requirement; this one checks that the requirement is the same number
whichever door you come through.
"""

from unittest.mock import MagicMock

import pytest

from python.framework.workers.core.bollinger_worker import BollingerWorker
from python.framework.workers.core.ma_trend_worker import MaTrendWorker
from python.framework.workers.core.macd_worker import MacdWorker
from python.framework.workers.core.obv_worker import ObvWorker
from python.framework.workers.core.rsi_worker import RsiWorker

# One representative config per worker, covering the averaging choices that change the
# answer. A worker whose warmup depends on `ma_type` appears under BOTH settings.
CASES = [
    ('rsi', RsiWorker, {'periods': {'M5': 14}}),
    ('rsi_long', RsiWorker, {'periods': {'H1': 50}}),
    ('obv', ObvWorker, {'periods': {'M5': 20}}),
    ('macd', MacdWorker, {'periods': {'M5': 35}, 'fast_period': 12,
                          'slow_period': 26, 'signal_period': 9}),
    ('bollinger_sma', BollingerWorker, {'periods': {'M30': 20}, 'deviation': 2.0,
                                        'ma_type': 'sma'}),
    ('bollinger_ema', BollingerWorker, {'periods': {'M30': 20}, 'deviation': 2.0,
                                        'ma_type': 'ema'}),
    ('ma_trend_sma', MaTrendWorker, {'periods': {'H1': 50}, 'ma_type': 'sma',
                                     'neutral_band': 0.05}),
    ('ma_trend_ema', MaTrendWorker, {'periods': {'H1': 50}, 'ma_type': 'ema',
                                     'neutral_band': 0.05}),
]


@pytest.mark.parametrize('label, worker_class, config', CASES, ids=[c[0] for c in CASES])
def test_the_classmethod_and_the_instance_agree(label, worker_class, config):
    instance = worker_class(name=f'test_{label}', parameters=config, logger=MagicMock())

    from_pipeline = worker_class.calculate_requirements(config)
    from_runtime = instance.get_warmup_requirements()

    assert from_pipeline == from_runtime, (
        f'{worker_class.__name__} answers differently depending on who asks: the pipeline '
        f'would load {from_pipeline} while the runtime demands {from_runtime}'
    )


@pytest.mark.parametrize('label, worker_class, config', CASES, ids=[c[0] for c in CASES])
def test_the_requirement_covers_every_configured_timeframe(label, worker_class, config):
    """A dropped timeframe is the other way this can go wrong, and it is just as silent."""
    requirement = worker_class.calculate_requirements(config)

    assert set(requirement) == set(config['periods'])
    assert all(bars >= period for (tf, bars), period
               in zip(requirement.items(), config['periods'].values())), (
        f'{worker_class.__name__} asks for fewer bars than its own period: {requirement}'
    )


class TestARecursiveAverageAsksForMoreThanItsPeriod:
    """
    The property the whole change rests on: an EMA or an RMA over exactly `period` values
    returns its own seed. A worker that asks only for its period is asking for a number it
    cannot compute.
    """

    def test_bollinger_asks_for_more_when_its_midline_is_exponential(self):
        simple = BollingerWorker.calculate_requirements(
            {'periods': {'M30': 20}, 'deviation': 2.0, 'ma_type': 'sma'})
        exponential = BollingerWorker.calculate_requirements(
            {'periods': {'M30': 20}, 'deviation': 2.0, 'ma_type': 'ema'})

        assert simple['M30'] == 20
        assert exponential['M30'] > simple['M30']

    def test_ma_trend_defaults_to_the_exponential_requirement(self):
        # Its ma_type default is 'ema', so a config that omits the key must still get the
        # larger window — the case that actually shipped.
        requirement = MaTrendWorker.calculate_requirements({'periods': {'H1': 50}})

        assert requirement['H1'] > 50

    def test_rsi_asks_for_wilders_window_plus_the_delta_bar(self):
        requirement = RsiWorker.calculate_requirements({'periods': {'M5': 14}})

        assert requirement['M5'] > 14 + 1
