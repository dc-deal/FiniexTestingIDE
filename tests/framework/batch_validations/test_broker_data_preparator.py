"""
FiniexTestingIDE - BrokerDataPreparator Unit Tests

Covers get_valid_broker_scenario_map() — the (broker_type, symbol) pair filter
that prevents same-symbol-on-different-brokers from polluting the reporting map.
"""

import json
from unittest.mock import MagicMock

import pytest

from python.configuration.market_config_manager import MarketConfigManager
from python.framework.data_preparation.broker_data_preparator import BrokerDataPreparator
from python.framework.factory.broker_config_factory import BrokerConfigFactory
from python.framework.types.scenario_types.scenario_set_types import (
    BrokerScenarioInfo,
    SingleScenario,
)
from python.framework.types.trading_env_types.broker_types import BrokerType


def _make_valid_scenario(name: str, symbol: str, broker_type: BrokerType) -> MagicMock:
    s = MagicMock(spec=SingleScenario)
    s.name = name
    s.symbol = symbol
    s.broker_type = broker_type
    return s


def _make_broker_info(config_path: str, scenarios: list, symbols: set) -> BrokerScenarioInfo:
    return BrokerScenarioInfo(
        config_path=config_path,
        scenarios=scenarios,
        symbols=symbols,
        broker_config=MagicMock(),
    )


class TestGetValidBrokerScenarioMap:
    """get_valid_broker_scenario_map — broker map filtered to valid scenarios only."""

    def _make_preparator(self, broker_map: dict) -> BrokerDataPreparator:
        preparator = BrokerDataPreparator([], MagicMock())
        preparator._broker_scenario_map = broker_map
        return preparator

    def test_same_symbol_different_brokers_only_valid_broker_survives(self):
        # Critical case: DASHUSD on KRAKEN (valid) and MT5 (invalid).
        # Symbol name alone ('DASHUSD') is not sufficient — must filter by (broker_type, symbol) pair.
        broker_map = {
            BrokerType.KRAKEN_SPOT: _make_broker_info('kraken.json', ['dash_kraken'], {'DASHUSD'}),
            BrokerType.MT5_FOREX: _make_broker_info('mt5.json', ['dash_mt5'], {'DASHUSD'}),
        }
        preparator = self._make_preparator(broker_map)
        valid_scenarios = [_make_valid_scenario('dash_kraken', 'DASHUSD', BrokerType.KRAKEN_SPOT)]

        result = preparator.get_valid_broker_scenario_map(valid_scenarios)

        assert BrokerType.KRAKEN_SPOT in result
        assert BrokerType.MT5_FOREX not in result
        assert result[BrokerType.KRAKEN_SPOT].symbols == {'DASHUSD'}
        assert result[BrokerType.KRAKEN_SPOT].scenarios == ['dash_kraken']

    def test_all_valid_scenarios_full_map_returned(self):
        broker_map = {
            BrokerType.KRAKEN_SPOT: _make_broker_info(
                'kraken.json', ['btc_run', 'eth_run'], {'BTCUSD', 'ETHUSD'}
            ),
        }
        preparator = self._make_preparator(broker_map)
        valid_scenarios = [
            _make_valid_scenario('btc_run', 'BTCUSD', BrokerType.KRAKEN_SPOT),
            _make_valid_scenario('eth_run', 'ETHUSD', BrokerType.KRAKEN_SPOT),
        ]

        result = preparator.get_valid_broker_scenario_map(valid_scenarios)

        assert BrokerType.KRAKEN_SPOT in result
        assert result[BrokerType.KRAKEN_SPOT].symbols == {'BTCUSD', 'ETHUSD'}
        assert set(result[BrokerType.KRAKEN_SPOT].scenarios) == {'btc_run', 'eth_run'}

    def test_all_invalid_scenarios_empty_map_returned(self):
        broker_map = {
            BrokerType.KRAKEN_SPOT: _make_broker_info('kraken.json', ['btc_bad'], {'BTCUSD'}),
        }
        preparator = self._make_preparator(broker_map)

        result = preparator.get_valid_broker_scenario_map([])

        assert result == {}


class TestTheBacktestReadsItsFeesFromTheSeed:
    """
    A backtest's fee rate comes from the git-tracked seed, never from the runtime cache (#337).

    The seam this closes: `market_config.json` puts `kraken_spot` in `config_mode: dynamic`, and
    the dynamic branch loads the whole broker config from `data/runtime/brokers/…` — a gitignored,
    machine-local cache. A fee rate arriving that way makes two backtests over identical data
    disagree, and nothing in either run can say why: `config_hash` is computed from the `symbols`
    block alone, so it stays identical. One `broker_config_cli.py sync` is all it takes.

    Today the two happen to agree, because the fetcher HARDCODES the rates rather than fetching
    them — so the risk is structural rather than active. #337 is the change that would make it
    active, which is why the split lands first.

    Symbol specifications keep coming from the cache: they are expensive to fetch and they really
    do change at the venue. The split is by CONCERN, not by config mode.
    """


    def _cache_with_fees(self, maker: float, taker: float) -> dict:
        """
        A runtime-cache document carrying one symbol and a chosen fee block.

        Args:
            maker: Maker percentage the cache claims
            taker: Taker percentage the cache claims

        Returns:
            A cache dict shaped like the real one
        """
        seed_path = MarketConfigManager().get_broker_config_path('kraken_spot')
        seed = BrokerConfigFactory.build_broker_config(seed_path)
        raw = dict(seed.adapter.broker_config)
        # The real cache carries this explicitly; the seed is detected from its folder
        # instead, and this fixture is loaded under the cache path.
        raw['broker_type'] = 'kraken_spot'
        raw['fee_structure'] = {
            'model': 'maker_taker', 'maker_fee': maker, 'taker_fee': taker,
            'fee_currency': 'quote',
        }
        return raw

    def test_a_cache_claiming_other_rates_does_not_reach_the_backtest(self, monkeypatch):
        """The whole point: the cache may say anything, the run uses the seed."""
        monkeypatch.setattr(
            'python.framework.data_preparation.broker_data_preparator.load_runtime_cache',
            lambda broker_type: self._cache_with_fees(9.99, 9.99))

        config = BrokerDataPreparator._load_dynamic_broker_config('kraken_spot', ['ETHUSD'])

        # Read from the seed rather than repeated here: the rate is re-frozen whenever the
        # account's tier moves, and a copy in the test would be a third place declaring it.
        seed = BrokerDataPreparator._seed_fee_structure('kraken_spot')
        assert config.adapter.get_maker_fee() == seed['maker_fee']
        assert config.adapter.get_taker_fee() == seed['taker_fee']
        assert config.adapter.get_maker_fee() != 9.99, 'the cache must not have won'

    def test_the_symbols_still_come_from_the_cache(self, monkeypatch):
        """
        The split must not throw the baby out: symbol specs are the expensive half and stay
        cache-sourced.

        Pinned against the CACHE rather than the seed, so a change that routed symbols
        through the seed as well would fail here — the cache declares a volume_min the seed
        does not.
        """
        cache = self._cache_with_fees(9.99, 9.99)
        cache['symbols']['ETHUSD']['volume_min'] = 0.0777
        monkeypatch.setattr(
            'python.framework.data_preparation.broker_data_preparator.load_runtime_cache',
            lambda broker_type: cache)

        config = BrokerDataPreparator._load_dynamic_broker_config('kraken_spot', ['ETHUSD'])

        assert config.get_symbol_specification('ETHUSD').volume_min == 0.0777

    def test_a_seed_without_a_fee_structure_refuses_rather_than_falling_back(
            self, monkeypatch, tmp_path):
        """
        Falling back to the cache here would re-open the exact divergence the split prevents,
        so the absence is an error and the message NAMES the seed.

        The seed path is redirected rather than the reader stubbed — stubbing the reader skips
        the refusal entirely. Which LAYER refuses is not pinned here, and today it is the
        adapter: `_validate_config` runs while the factory builds the config, so it reaches
        the missing block before the factory's own check does. What matters to the caller is
        that a backtest stops instead of silently pricing itself from the cache.
        """
        seed = self._cache_with_fees(0.25, 0.4)
        seed.pop('fee_structure')
        seed_path = tmp_path / 'seed_without_fees.json'
        seed_path.write_text(json.dumps(seed), encoding='utf-8')
        monkeypatch.setattr(
            MarketConfigManager, 'get_broker_config_path',
            lambda self, broker_type: str(seed_path))
        monkeypatch.setattr(
            'python.framework.data_preparation.broker_data_preparator.load_runtime_cache',
            lambda broker_type: self._cache_with_fees(9.99, 9.99))

        with pytest.raises(ValueError, match='fee_structure'):
            BrokerDataPreparator._load_dynamic_broker_config('kraken_spot', ['ETHUSD'])
