"""
FiniexTestingIDE - BrokerConfigFactory Unit Tests

Covers:
- _validate_symbol_integrity(): base/quote must match the symbol key
- _inject_symbols_hash(): 8-char SHA256 of symbols block; stable across meta-only changes
"""

import pytest
from pathlib import Path

from python.framework.factory.broker_config_factory import BrokerConfigFactory


def _make_symbol(symbol: str, base: str, quote: str) -> dict:
    return {
        'base_currency': base,
        'quote_currency': quote,
        'volume_min': 0.001,
        'volume_max': 100.0,
        'volume_step': 0.001,
        'contract_size': 1,
        'tick_size': 0.1,
        'margin_currency': quote,
        'swap_long': 0.0,
        'swap_short': 0.0,
        'swap_mode': 'points',
        'trade_allowed': True,
        'description': symbol,
        'kraken_pair_name': symbol,
        '_active': True,
    }


_DUMMY_PATH = Path('configs/brokers/test/test_broker_config.json')


class TestSymbolIntegrityValidation:
    """_validate_symbol_integrity — base/quote must match symbol key."""

    def test_valid_config_passes(self):
        config = {'symbols': {'BTCUSD': _make_symbol('BTCUSD', 'BTC', 'USD')}}
        BrokerConfigFactory._validate_symbol_integrity(config, _DUMMY_PATH)

    def test_wrong_base_currency_raises(self):
        config = {'symbols': {'DASHUSD': _make_symbol('DASHUSD', 'ETH', 'USD')}}
        with pytest.raises(ValueError, match="DASHUSD"):
            BrokerConfigFactory._validate_symbol_integrity(config, _DUMMY_PATH)

    def test_wrong_quote_currency_raises(self):
        config = {'symbols': {'BTCUSD': _make_symbol('BTCUSD', 'BTC', 'EUR')}}
        with pytest.raises(ValueError, match="BTCUSD"):
            BrokerConfigFactory._validate_symbol_integrity(config, _DUMMY_PATH)

    def test_7char_symbol_validates_correctly(self):
        # DASHUSD is 7 chars — base should be DASH, quote USD
        config = {'symbols': {'DASHUSD': _make_symbol('DASHUSD', 'DASH', 'USD')}}
        BrokerConfigFactory._validate_symbol_integrity(config, _DUMMY_PATH)

    def test_missing_currency_fields_skipped(self):
        # Entries without base_currency or quote_currency must not crash
        config = {'symbols': {'BTCUSD': {'volume_min': 0.001}}}
        BrokerConfigFactory._validate_symbol_integrity(config, _DUMMY_PATH)


class TestConfigHashComputation:
    """_inject_symbols_hash — hash is computed from symbols block only."""

    def test_hash_is_8_chars(self):
        config = {'symbols': {'BTCUSD': _make_symbol('BTCUSD', 'BTC', 'USD')}}
        BrokerConfigFactory._inject_symbols_hash(config)
        assert len(config['_config_meta']['symbols_hash']) == 8

    def test_hash_stable_for_same_symbols(self):
        config_a = {'symbols': {'BTCUSD': _make_symbol('BTCUSD', 'BTC', 'USD')}}
        config_b = {'symbols': {'BTCUSD': _make_symbol('BTCUSD', 'BTC', 'USD')}}
        BrokerConfigFactory._inject_symbols_hash(config_a)
        BrokerConfigFactory._inject_symbols_hash(config_b)
        assert config_a['_config_meta']['symbols_hash'] == config_b['_config_meta']['symbols_hash']

    def test_hash_changes_when_symbol_spec_changes(self):
        config_a = {'symbols': {'BTCUSD': _make_symbol('BTCUSD', 'BTC', 'USD')}}
        config_b = {'symbols': {'BTCUSD': {**_make_symbol('BTCUSD', 'BTC', 'USD'), 'volume_min': 0.01}}}
        BrokerConfigFactory._inject_symbols_hash(config_a)
        BrokerConfigFactory._inject_symbols_hash(config_b)
        assert config_a['_config_meta']['symbols_hash'] != config_b['_config_meta']['symbols_hash']

    def test_hash_stable_when_only_meta_changes(self):
        # Changing last_fetched in _config_meta must not affect the hash
        symbols = {'BTCUSD': _make_symbol('BTCUSD', 'BTC', 'USD')}
        config_a = {'symbols': symbols, '_config_meta': {'last_fetched': '2026-01-01T00:00:00Z'}}
        config_b = {'symbols': symbols, '_config_meta': {'last_fetched': '2026-06-01T00:00:00Z'}}
        BrokerConfigFactory._inject_symbols_hash(config_a)
        BrokerConfigFactory._inject_symbols_hash(config_b)
        assert config_a['_config_meta']['symbols_hash'] == config_b['_config_meta']['symbols_hash']
