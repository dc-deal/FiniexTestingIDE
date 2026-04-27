"""
FiniexTestingIDE - BrokerDataPreparator Unit Tests

Covers get_valid_broker_scenario_map() — the (broker_type, symbol) pair filter
that prevents same-symbol-on-different-brokers from polluting the reporting map.
"""

from unittest.mock import MagicMock

from python.framework.data_preparation.broker_data_preparator import BrokerDataPreparator
from python.framework.types.trading_env_types.broker_types import BrokerType
from python.framework.types.scenario_types.scenario_set_types import BrokerScenarioInfo, SingleScenario


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
