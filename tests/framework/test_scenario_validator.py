"""
FiniexTestingIDE - ScenarioValidator Unit Tests

Covers detect_quote_currency() and detect_base_currency() for standard
6-char symbols and variable-length symbols such as DASHUSD (7 chars).
"""

import pytest

from python.framework.validators.scenario_validator import ScenarioValidator


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
