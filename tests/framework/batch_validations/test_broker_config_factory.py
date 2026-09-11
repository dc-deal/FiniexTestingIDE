"""
FiniexTestingIDE - BrokerConfigFactory Unit Tests

Covers:
- _validate_symbol_integrity(): base/quote must match the symbol key
- _inject_symbols_hash(): 8-char SHA256 of symbols block; stable across meta-only changes
"""

import json
from datetime import datetime, timezone
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


class TestTheFreezeDateIsReadableBack:
    """
    A fee rate is a declared ASSUMPTION, so the seed records when it was frozen (#505
    follow-up) — and something has to be able to read that back, or the date is decoration.

    Who needs it: a live session compares the declared rate against the venue on every start
    and warns on divergence. Someone running only BACKTESTS never sees that warning, and the
    seed can rot indefinitely for them. `store_cli.py catalog` asks this question for that
    reader, next to the expired release certificates — the same shape, because it is the same
    kind of statement.
    """

    def _seed(self, tmp_path, block) -> str:
        """
        A broker config file carrying a chosen freeze block.

        Args:
            tmp_path: pytest temporary directory
            block: The `_fee_structure_frozen` value, or None to omit it

        Returns:
            Path to the written file
        """
        raw = {'symbols': {}, 'fee_structure': {'model': 'maker_taker'}}
        if block is not None:
            raw['_fee_structure_frozen'] = block
        path = tmp_path / 'seed.json'
        path.write_text(json.dumps(raw), encoding='utf-8')
        return str(path)

    def test_the_age_is_whole_days_since_the_stamp(self, tmp_path):
        path = self._seed(tmp_path, {'date': '2026-06-01'})

        stamped, days = BrokerConfigFactory.frozen_fee_age_days(
            path, now=datetime(2026, 9, 9, tzinfo=timezone.utc))

        assert stamped == '2026-06-01'
        assert days == 100

    def test_a_file_with_no_freeze_block_answers_None_not_zero(self, tmp_path):
        """
        An absence is not an age. Reporting zero would say "frozen today" about a file that
        never claimed a date — which is the more comfortable of the two wrong answers.
        """
        assert BrokerConfigFactory.frozen_fee_age_days(self._seed(tmp_path, None)) is None

    def test_the_stamp_lives_OUTSIDE_the_hashed_fee_block(self, tmp_path):
        """
        Why it is a sibling of `fee_structure` and not a field in it: `config_hash` is
        computed over the fee block, so a provenance note inside it would move the
        reproducibility anchor without changing a single price.
        """
        without = {'symbols': {}, 'fee_structure': {'maker_fee': 0.4, 'taker_fee': 0.8}}
        with_note = dict(without)
        with_note['_fee_structure_frozen'] = {'date': '2026-09-08', 'source': 'measured'}

        BrokerConfigFactory._inject_config_hashes(without)
        BrokerConfigFactory._inject_config_hashes(with_note)

        assert (without['_config_meta']['config_hash']
                == with_note['_config_meta']['config_hash'])

    def test_the_shipped_kraken_seed_carries_one(self, tmp_path):
        """The rule is only worth anything if the file that matters actually follows it."""
        age = BrokerConfigFactory.frozen_fee_age_days(
            'configs/brokers/kraken/kraken_spot_broker_config.json')

        assert age is not None, 'the re-frozen Kraken seed must say when it was frozen'
        assert age[1] >= 0

