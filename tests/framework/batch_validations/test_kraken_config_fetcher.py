"""
Kraken Config Fetcher — Cache Behavior Tests.

Unit tests for the runtime cache logic in KrakenConfigFetcher:

- _merge_with_cache: existing symbols are not tombstoned; fresh symbols get
  _active:true and a _last_fetched timestamp.
- fetch_broker_config_with_cache: symbol-presence check — a fresh cache that
  does not contain the requested symbol triggers an API fetch and extends
  the cache rather than returning an incomplete result.

No real HTTP calls; all external I/O is patched or replaced with tmp_path files.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from python.configuration.autotrader.kraken_config_fetcher import (
    KrakenConfigFetcher,
    _merge_with_cache,
)


# =============================================================================
# HELPERS
# =============================================================================

def _make_cache_file(tmp_path: Path, symbols: dict) -> Path:
    """Write a minimal broker config cache file and return its path."""
    path = tmp_path / 'kraken_spot_broker_config.json'
    data = {
        '_config_meta': {
            'schema_version': '2.0',
            'last_fetched': '2026-01-01T00:00:00Z',
            'symbols_hash': 'deadbeef',
        },
        'broker_info': {'company': 'Kraken'},
        'symbols': symbols,
    }
    path.write_text(json.dumps(data))
    return path


def _make_fresh_dict(symbol: str, base: str, quote: str) -> dict:
    """Minimal single-symbol fresh API config dict."""
    return {
        'broker_info': {'company': 'Kraken'},
        '_config_meta': {},
        'symbols': {
            symbol: {
                'base_currency': base,
                'quote_currency': quote,
                'trade_allowed': True,
                'volume_min': 0.001,
                'volume_max': 10000.0,
            }
        },
    }


def _make_fetcher() -> KrakenConfigFetcher:
    """Create a KrakenConfigFetcher without loading real credentials."""
    fetcher = object.__new__(KrakenConfigFetcher)
    fetcher._logger = None
    fetcher._api_base_url = KrakenConfigFetcher.API_BASE
    fetcher._api_key = 'test_key'
    fetcher._api_secret = 'test_secret'
    return fetcher


# =============================================================================
# TestMergeWithCache
# =============================================================================

class TestMergeWithCache:
    """_merge_with_cache: no-tombstone merge, _last_fetched timestamps."""

    def test_existing_symbol_not_tombstoned(self, tmp_path):
        """Symbols already in the cache keep _active:true when a different symbol is added."""
        existing = {'ETHUSD': {'base_currency': 'ETH', 'quote_currency': 'USD', '_active': True}}
        cache_path = _make_cache_file(tmp_path, existing)

        fresh = _make_fresh_dict('DOTUSD', 'DOT', 'USD')
        result = _merge_with_cache(fresh, cache_path)

        assert result['symbols']['ETHUSD']['_active'] is True, \
            'ETHUSD must not be tombstoned when only DOTUSD is fetched.'

    def test_fresh_symbol_gets_active_and_last_fetched(self, tmp_path):
        """Freshly fetched symbol gets _active:true and a valid _last_fetched timestamp."""
        cache_path = tmp_path / 'cache.json'  # does not exist
        fresh = _make_fresh_dict('BTCUSD', 'BTC', 'USD')

        result = _merge_with_cache(fresh, cache_path)
        btc = result['symbols']['BTCUSD']

        assert btc['_active'] is True
        assert '_last_fetched' in btc, 'Fresh symbol must have _last_fetched field.'
        datetime.fromisoformat(btc['_last_fetched'].replace('Z', '+00:00'))  # must be valid ISO

    def test_existing_symbol_last_fetched_preserved(self, tmp_path):
        """Existing symbol keeps its original _last_fetched when not re-fetched."""
        original_ts = '2026-03-01T10:00:00Z'
        existing = {
            'ETHUSD': {
                'base_currency': 'ETH',
                'quote_currency': 'USD',
                '_active': True,
                '_last_fetched': original_ts,
            }
        }
        cache_path = _make_cache_file(tmp_path, existing)

        fresh = _make_fresh_dict('DOTUSD', 'DOT', 'USD')
        result = _merge_with_cache(fresh, cache_path)

        assert result['symbols']['ETHUSD']['_last_fetched'] == original_ts, \
            'Existing symbol _last_fetched must not change when a different symbol is fetched.'

    def test_no_cache_returns_fresh_symbols_only(self, tmp_path):
        """When no cache file exists, result contains only the fresh symbol."""
        cache_path = tmp_path / 'cache.json'  # does not exist
        fresh = _make_fresh_dict('SOLUSD', 'SOL', 'USD')

        result = _merge_with_cache(fresh, cache_path)

        assert list(result['symbols'].keys()) == ['SOLUSD']
        assert result['symbols']['SOLUSD']['_active'] is True


# =============================================================================
# TestFetchWithCacheSymbolCheck
# =============================================================================

class TestFetchWithCacheSymbolCheck:
    """fetch_broker_config_with_cache: lazy symbol addition to a fresh cache."""

    def test_fresh_cache_with_symbol_skips_api(self):
        """Symbol present in fresh cache (< 7 days) → no API call made."""
        cached = {
            '_config_meta': {'symbols_hash': 'abc12345'},
            'broker_info': {'company': 'Kraken'},
            'symbols': {'ETHUSD': {'base_currency': 'ETH', '_active': True}},
        }
        fetcher = _make_fetcher()

        with (
            patch('python.configuration.autotrader.kraken_config_fetcher._get_cache_age_days',
                  return_value=1.0),
            patch('python.configuration.autotrader.kraken_config_fetcher._load_json',
                  return_value=cached),
            patch.object(fetcher, 'fetch_broker_config') as mock_fetch,
        ):
            result = fetcher.fetch_broker_config_with_cache('ETHUSD', 'kraken_spot')

        mock_fetch.assert_not_called()
        assert 'ETHUSD' in result['symbols']

    def test_fresh_cache_missing_symbol_triggers_fetch(self):
        """Symbol absent from a fresh cache → API called, symbol merged into result."""
        cached = {
            '_config_meta': {
                'schema_version': '2.0',
                'last_fetched': '2026-05-04T12:00:00Z',
                'symbols_hash': 'abc12345',
            },
            'broker_info': {'company': 'Kraken'},
            'symbols': {'ETHUSD': {'base_currency': 'ETH', 'quote_currency': 'USD', '_active': True}},
        }
        dot_fresh = _make_fresh_dict('DOTUSD', 'DOT', 'USD')
        fetcher = _make_fetcher()

        with (
            patch('python.configuration.autotrader.kraken_config_fetcher._get_cache_age_days',
                  return_value=1.0),
            patch('python.configuration.autotrader.kraken_config_fetcher._load_json',
                  return_value=cached),
            patch('python.configuration.autotrader.kraken_config_fetcher._write_cache'),
            patch.object(fetcher, 'fetch_broker_config', return_value=dot_fresh) as mock_fetch,
        ):
            result = fetcher.fetch_broker_config_with_cache('DOTUSD', 'kraken_spot')

        mock_fetch.assert_called_once_with('DOTUSD', 'kraken_spot')
        assert 'DOTUSD' in result['symbols'], 'DOTUSD must be in result after fetch.'
        assert 'ETHUSD' in result['symbols'], 'ETHUSD must be preserved (no tombstoning).'
