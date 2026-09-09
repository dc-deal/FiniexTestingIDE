"""
FiniexTestingIDE - BrokerConfigFactory Unit Tests

Covers:
- _validate_symbol_integrity(): base/quote must match the symbol key
- _inject_symbols_hash(): 8-char SHA256 of symbols block; stable across meta-only changes
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from python.framework.factory.broker_config_factory import BrokerConfigFactory
from python.framework.trading_env.broker_config import BrokerConfig


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
        with pytest.raises(ValueError, match='DASHUSD'):
            BrokerConfigFactory._validate_symbol_integrity(config, _DUMMY_PATH)

    def test_wrong_quote_currency_raises(self):
        config = {'symbols': {'BTCUSD': _make_symbol('BTCUSD', 'BTC', 'EUR')}}
        with pytest.raises(ValueError, match='BTCUSD'):
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
    """_inject_config_hashes — `symbols_hash` is the symbol set alone."""

    def test_hash_is_8_chars(self):
        config = {'symbols': {'BTCUSD': _make_symbol('BTCUSD', 'BTC', 'USD')}}
        BrokerConfigFactory._inject_config_hashes(config)
        assert len(config['_config_meta']['symbols_hash']) == 8

    def test_hash_stable_for_same_symbols(self):
        config_a = {'symbols': {'BTCUSD': _make_symbol('BTCUSD', 'BTC', 'USD')}}
        config_b = {'symbols': {'BTCUSD': _make_symbol('BTCUSD', 'BTC', 'USD')}}
        BrokerConfigFactory._inject_config_hashes(config_a)
        BrokerConfigFactory._inject_config_hashes(config_b)
        assert config_a['_config_meta']['symbols_hash'] == config_b['_config_meta']['symbols_hash']

    def test_hash_changes_when_symbol_spec_changes(self):
        config_a = {'symbols': {'BTCUSD': _make_symbol('BTCUSD', 'BTC', 'USD')}}
        config_b = {'symbols': {'BTCUSD': {**_make_symbol('BTCUSD', 'BTC', 'USD'), 'volume_min': 0.01}}}
        BrokerConfigFactory._inject_config_hashes(config_a)
        BrokerConfigFactory._inject_config_hashes(config_b)
        assert config_a['_config_meta']['symbols_hash'] != config_b['_config_meta']['symbols_hash']

    def test_hash_stable_when_only_meta_changes(self):
        # Changing last_fetched in _config_meta must not affect the hash
        symbols = {'BTCUSD': _make_symbol('BTCUSD', 'BTC', 'USD')}
        config_a = {'symbols': symbols, '_config_meta': {'last_fetched': '2026-01-01T00:00:00Z'}}
        config_b = {'symbols': symbols, '_config_meta': {'last_fetched': '2026-06-01T00:00:00Z'}}
        BrokerConfigFactory._inject_config_hashes(config_a)
        BrokerConfigFactory._inject_config_hashes(config_b)
        assert config_a['_config_meta']['symbols_hash'] == config_b['_config_meta']['symbols_hash']


class TestTheReproducibilityHashCoversTheFees:
    """
    `config_hash` is the anchor the broker section of the report model carries — so it has to
    move with everything that changes what a run PRODUCES (#337).

    It used to be `symbols_hash`, the symbol set alone. A fee rate moves realised P&L on every
    trade, so two runs at different rates are different runs; under the old reading their
    recorded identity was byte-identical and nothing could tell them apart afterwards. Measured
    2026-09-08, that was not hypothetical: the declared rates were half the account's real tier,
    and correcting them changes every maker/taker backtest.

    `symbols_hash` keeps its own meaning — the config fetcher and its CLI print it to say
    whether a cache still describes the same instruments.
    """

    def _config(self, taker: float = 0.40) -> dict:
        """
        A minimal broker config with one symbol and a chosen taker rate.

        Args:
            taker: The taker percentage to declare

        Returns:
            A config dict ready for hash injection
        """
        return {
            'symbols': {'BTCUSD': _make_symbol('BTCUSD', 'BTC', 'USD')},
            'fee_structure': {
                'model': 'maker_taker', 'maker_fee': 0.25, 'taker_fee': taker,
                'fee_currency': 'quote',
            },
        }

    def test_a_changed_fee_rate_moves_the_config_hash(self):
        config_a = self._config(taker=0.40)
        config_b = self._config(taker=0.80)

        BrokerConfigFactory._inject_config_hashes(config_a)
        BrokerConfigFactory._inject_config_hashes(config_b)

        assert (config_a['_config_meta']['config_hash']
                != config_b['_config_meta']['config_hash'])

    def test_a_changed_fee_rate_leaves_the_SYMBOLS_hash_alone(self):
        """The two hashes answer different questions and must not blur into one."""
        config_a = self._config(taker=0.40)
        config_b = self._config(taker=0.80)

        BrokerConfigFactory._inject_config_hashes(config_a)
        BrokerConfigFactory._inject_config_hashes(config_b)

        assert (config_a['_config_meta']['symbols_hash']
                == config_b['_config_meta']['symbols_hash'])

    def test_it_is_stable_when_nothing_that_matters_changed(self):
        config_a = self._config()
        config_b = self._config()
        config_b['_config_meta'] = {'last_fetched': '2026-09-08T00:00:00Z'}

        BrokerConfigFactory._inject_config_hashes(config_a)
        BrokerConfigFactory._inject_config_hashes(config_b)

        assert (config_a['_config_meta']['config_hash']
                == config_b['_config_meta']['config_hash'])

    def test_the_fee_covering_hash_is_the_one_reported(self):
        """
        The preference IS the change — without this the property may quietly go back to
        reporting `symbols_hash`, and a fee-rate difference disappears from the record again
        while every gate stays green.
        """
        adapter = MagicMock()
        adapter.broker_config = {
            '_config_meta': {'symbols_hash': 'aaaaaaaa', 'config_hash': 'bbbbbbbb'},
        }
        config = BrokerConfig.__new__(BrokerConfig)
        config.adapter = adapter

        assert config.config_hash == 'bbbbbbbb'

    def test_a_config_written_before_the_split_still_reports_an_identity(self):
        """
        An older cache carries only `symbols_hash`. Reporting nothing would be worse than
        reporting the older, narrower identity — so the property falls back.
        """
        adapter = MagicMock()
        adapter.broker_config = {'_config_meta': {'symbols_hash': 'deadbeef'}}
        adapter.get_broker_name.return_value = 'x'
        adapter.get_order_capabilities.return_value = MagicMock()
        config = BrokerConfig.__new__(BrokerConfig)
        config.adapter = adapter

        assert config.config_hash == 'deadbeef'
