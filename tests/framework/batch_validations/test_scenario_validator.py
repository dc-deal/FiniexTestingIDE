"""
FiniexTestingIDE - ScenarioValidator Unit Tests

Covers:
- detect_quote_currency() and detect_base_currency() for standard 6-char symbols
  and variable-length symbols such as DASHUSD (7 chars)
- validate_scenario_symbols() — broker config symbol registration check
"""

import pytest
from unittest.mock import MagicMock

from python.framework.validators.scenario_validator import ScenarioValidator
from python.framework.types.trading_env_types.broker_types import BrokerType
from python.framework.types.scenario_types.scenario_set_types import BrokerScenarioInfo, SingleScenario


class TestDetectQuoteCurrency:
    """detect_quote_currency — known-quote suffix detection."""

    def test_standard_usd_pair(self):
        assert ScenarioValidator.detect_quote_currency('EURUSD') == 'USD'

    def test_standard_jpy_pair(self):
        assert ScenarioValidator.detect_quote_currency('USDJPY') == 'JPY'

    def test_standard_eur_pair(self):
        assert ScenarioValidator.detect_quote_currency('GBPEUR') == 'EUR'

    def test_crypto_standard_6char(self):
        assert ScenarioValidator.detect_quote_currency('BTCUSD') == 'USD'

    def test_crypto_dashusd_7char(self):
        # DASHUSD has 7 chars — previously raised ValueError
        assert ScenarioValidator.detect_quote_currency('DASHUSD') == 'USD'

    def test_lowercase_symbol(self):
        assert ScenarioValidator.detect_quote_currency('dashusd') == 'USD'

    def test_fallback_unknown_suffix(self):
        # Symbol with no known quote suffix → last 3 chars
        assert ScenarioValidator.detect_quote_currency('ABCXYZ') == 'XYZ'


class TestDetectBaseCurrency:
    """detect_base_currency — known-quote suffix detection."""

    def test_standard_eur_base(self):
        assert ScenarioValidator.detect_base_currency('EURUSD') == 'EUR'

    def test_standard_usd_base(self):
        assert ScenarioValidator.detect_base_currency('USDJPY') == 'USD'

    def test_crypto_btc_base(self):
        assert ScenarioValidator.detect_base_currency('BTCUSD') == 'BTC'

    def test_crypto_dashusd_7char(self):
        # DASHUSD → base is 'DASH', not 'DAS' (old wrong result via symbol[:3])
        assert ScenarioValidator.detect_base_currency('DASHUSD') == 'DASH'

    def test_lowercase_symbol(self):
        assert ScenarioValidator.detect_base_currency('dashusd') == 'DASH'

    def test_fallback_unknown_suffix(self):
        # Symbol with no known quote suffix → all but last 3 chars
        assert ScenarioValidator.detect_base_currency('ABCXYZ') == 'ABC'


class TestValidateScenarioSymbols:
    """validate_scenario_symbols — symbol registration check against broker config."""

    def _make_scenario(self, name: str, symbol: str, broker_type: BrokerType) -> MagicMock:
        scenario = MagicMock(spec=SingleScenario)
        scenario.name = name
        scenario.symbol = symbol
        scenario.broker_type = broker_type
        scenario.data_broker_type = broker_type.value
        scenario.validation_result = []
        return scenario

    def _make_broker_map(self, broker_type: BrokerType, known_symbols: list) -> dict:
        broker_config = MagicMock()
        def get_symbol_spec(symbol):
            if symbol in known_symbols:
                return MagicMock()
            raise ValueError(f"Symbol '{symbol}' not found")
        broker_config.get_symbol_specification.side_effect = get_symbol_spec

        broker_info = MagicMock(spec=BrokerScenarioInfo)
        broker_info.broker_config = broker_config
        return {broker_type: broker_info}

    def test_valid_symbol_no_validation_result(self):
        scenario = self._make_scenario('btc_test', 'BTCUSD', BrokerType.KRAKEN_SPOT)
        broker_map = self._make_broker_map(BrokerType.KRAKEN_SPOT, ['BTCUSD'])
        logger = MagicMock()

        ScenarioValidator.validate_scenario_symbols([scenario], logger, broker_map)

        assert len(scenario.validation_result) == 0
        logger.error.assert_not_called()

    def test_unknown_symbol_marks_scenario_invalid(self):
        scenario = self._make_scenario('dash_test', 'DASHUSD', BrokerType.MT5_FOREX)
        broker_map = self._make_broker_map(BrokerType.MT5_FOREX, [])
        logger = MagicMock()

        ScenarioValidator.validate_scenario_symbols([scenario], logger, broker_map)

        assert len(scenario.validation_result) == 1
        assert scenario.validation_result[0].is_valid is False
        assert 'DASHUSD' in scenario.validation_result[0].errors[0]
        logger.error.assert_called_once()

    def test_broker_not_in_map_skips_scenario(self):
        # Broker not in map → skip silently (broker prep may have failed upstream)
        scenario = self._make_scenario('btc_test', 'BTCUSD', BrokerType.BINANCE_FUTURES)
        broker_map = self._make_broker_map(BrokerType.KRAKEN_SPOT, ['BTCUSD'])
        logger = MagicMock()

        ScenarioValidator.validate_scenario_symbols([scenario], logger, broker_map)

        assert len(scenario.validation_result) == 0
        logger.error.assert_not_called()
