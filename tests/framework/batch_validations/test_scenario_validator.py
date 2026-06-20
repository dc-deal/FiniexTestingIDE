"""
FiniexTestingIDE - ScenarioValidator Unit Tests

Covers:
- detect_quote_currency() and detect_base_currency() for standard 6-char symbols
  and variable-length symbols such as DASHUSD (7 chars)
- validate_scenario_symbols() — broker config symbol registration check
- validate_account_currency() — explicit override compatibility (V1 cross-currency guard)
- set_scenario_account_currency() — derivation + spot quote-normalization
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


def _account_scenario(name, symbol, broker_type, balances, account_currency=None):
    """Build a MagicMock SingleScenario with a real trade_simulator_config dict."""
    scenario = MagicMock(spec=SingleScenario)
    scenario.name = name
    scenario.symbol = symbol
    scenario.broker_type = broker_type
    tsc = {'balances': balances}
    if account_currency is not None:
        tsc['account_currency'] = account_currency
    scenario.trade_simulator_config = tsc
    scenario.validation_result = []
    return scenario


class TestValidateAccountCurrency:
    """validate_account_currency — explicit override compatibility (V1 cross-currency guard).

    Empty broker_map → _get_symbol_currencies uses the known-quote-suffix heuristic,
    so no broker config is needed.
    """

    def test_explicit_incompatible_marks_invalid(self):
        # 'BTC' on a forex pair is neither base (GBP) nor quote (USD) → invalid
        scenario = _account_scenario('gbp', 'GBPUSD', BrokerType.MT5_FOREX, {'USD': 10000}, 'BTC')
        logger = MagicMock()

        ScenarioValidator.validate_account_currency(scenario, logger, {})

        assert len(scenario.validation_result) == 1
        assert scenario.validation_result[0].is_valid is False
        assert 'BTC' in scenario.validation_result[0].errors[0]
        logger.error.assert_called_once()

    def test_explicit_quote_is_valid(self):
        scenario = _account_scenario('gbp', 'GBPUSD', BrokerType.MT5_FOREX, {'USD': 10000}, 'USD')
        logger = MagicMock()

        ScenarioValidator.validate_account_currency(scenario, logger, {})

        assert len(scenario.validation_result) == 0

    def test_explicit_base_is_valid(self):
        # base-denominated margin account (MT5 style) is allowed
        scenario = _account_scenario('gbp', 'GBPUSD', BrokerType.MT5_FOREX, {'GBP': 10000}, 'GBP')
        logger = MagicMock()

        ScenarioValidator.validate_account_currency(scenario, logger, {})

        assert len(scenario.validation_result) == 0

    def test_balances_mismatch_short_circuits(self):
        # No balance matches symbol → invalid on balances; explicit check not reached
        scenario = _account_scenario('gbp', 'GBPUSD', BrokerType.MT5_FOREX, {'CHF': 10000}, 'CHF')
        logger = MagicMock()

        ScenarioValidator.validate_account_currency(scenario, logger, {})

        assert len(scenario.validation_result) == 1
        assert 'No balance currency matches' in scenario.validation_result[0].errors[0]


class TestSetScenarioAccountCurrency:
    """set_scenario_account_currency — derivation + spot quote-normalization."""

    def test_spot_base_normalized_to_quote_with_warning(self):
        # Spot BTCUSD with explicit base 'BTC' → normalized to quote 'USD' + warning
        scenario = _account_scenario(
            'btc', 'BTCUSD', BrokerType.KRAKEN_SPOT, {'USD': 10000, 'BTC': 0.0}, 'BTC')
        logger = MagicMock()

        ScenarioValidator.set_scenario_account_currency(logger, [scenario], {})

        assert scenario.account_currency == 'USD'
        warnings = [w for r in scenario.validation_result if r.is_valid for w in r.warnings]
        assert len(warnings) == 1 and 'normalized' in warnings[0]
        logger.warning.assert_called_once()

    def test_spot_quote_no_warning(self):
        # Spot, quote already → no normalization, no warning
        scenario = _account_scenario(
            'btc', 'BTCUSD', BrokerType.KRAKEN_SPOT, {'USD': 10000, 'BTC': 0.0})
        logger = MagicMock()

        ScenarioValidator.set_scenario_account_currency(logger, [scenario], {})

        assert scenario.account_currency == 'USD'
        assert scenario.validation_result == []
        logger.warning.assert_not_called()

    def test_margin_base_kept_no_warning(self):
        # Margin GBPUSD with base account 'GBP' → kept (MT5 style), no normalization
        scenario = _account_scenario('gbp', 'GBPUSD', BrokerType.MT5_FOREX, {'GBP': 10000}, 'GBP')
        logger = MagicMock()

        ScenarioValidator.set_scenario_account_currency(logger, [scenario], {})

        assert scenario.account_currency == 'GBP'
        assert scenario.validation_result == []
        logger.warning.assert_not_called()
