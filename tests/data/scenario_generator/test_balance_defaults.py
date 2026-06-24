"""
Generator Balance Defaults Tests.

The generator seeds a starting balance in the symbol's quote currency so every generation path
(blocks saver + profile loader) produces a set that validates and runs out of the box. The quote
is resolved AUTHORITATIVELY from the broker config (#265) — a missing/inconsistent split raises a
`SymbolCurrencyError` (FiniexError), never a silent guess.
"""
import pytest

from python.framework.exceptions.finiex_error import FiniexError
from python.framework.exceptions.generator_errors import SymbolCurrencyError
from python.scenario.generator.balance_defaults import (
    DEFAULT_QUOTE_BALANCE, _validate_symbol_currencies, ensure_quote_balance,
    resolve_quote_currency, resolve_symbol_currencies)


class TestEnsureQuoteBalance:
    def test_adds_quote(self):
        assert ensure_quote_balance({}, 'USD')['balances'] == {'USD': DEFAULT_QUOTE_BALANCE}

    def test_none_config_creates_balances(self):
        assert ensure_quote_balance(None, 'JPY')['balances'] == {'JPY': DEFAULT_QUOTE_BALANCE}

    def test_does_not_clobber_existing(self):
        assert ensure_quote_balance({'balances': {'USD': 500.0}}, 'USD')['balances']['USD'] == 500.0

    def test_adds_alongside_other_currency(self):
        out = ensure_quote_balance({'balances': {'EUR': 200.0}}, 'USD')
        assert out['balances'] == {'EUR': 200.0, 'USD': DEFAULT_QUOTE_BALANCE}

    def test_preserves_other_keys_and_no_mutation(self):
        original = {'account_currency': 'USD', 'balances': {}}
        out = ensure_quote_balance(original, 'USD')
        assert out['account_currency'] == 'USD'
        assert original == {'account_currency': 'USD', 'balances': {}}  # input untouched


class TestResolveSymbolCurrencies:
    def test_authoritative_quote_from_broker_config(self):
        # Reads the real kraken_spot broker config (ETHUSD -> ETH / USD)
        assert resolve_quote_currency('ETHUSD', 'kraken_spot') == 'USD'
        assert resolve_symbol_currencies('ETHUSD', 'kraken_spot') == ('ETH', 'USD')

    def test_dotusd_split(self):
        assert resolve_symbol_currencies('DOTUSD', 'kraken_spot') == ('DOT', 'USD')

    def test_unknown_symbol_raises(self):
        with pytest.raises(SymbolCurrencyError):
            resolve_quote_currency('NOPEZZZ', 'kraken_spot')


class TestValidateSymbolCurrencies:
    def test_valid_split_passes(self):
        _validate_symbol_currencies('DOTUSD', 'DOT', 'USD', 'kraken_spot')  # no raise

    def test_missing_base_raises(self):
        with pytest.raises(SymbolCurrencyError):
            _validate_symbol_currencies('DOTUSD', '', 'USD', 'kraken_spot')

    def test_missing_quote_raises(self):
        with pytest.raises(SymbolCurrencyError):
            _validate_symbol_currencies('DOTUSD', 'DOT', '', 'kraken_spot')

    def test_mismatch_with_symbol_raises(self):
        with pytest.raises(SymbolCurrencyError):
            _validate_symbol_currencies('DOTUSD', 'DOT', 'EUR', 'kraken_spot')

    def test_is_finiex_and_value_error(self):
        # multiple inheritance (§10) — catchable as both FiniexError and ValueError
        assert issubclass(SymbolCurrencyError, FiniexError)
        assert issubclass(SymbolCurrencyError, ValueError)
