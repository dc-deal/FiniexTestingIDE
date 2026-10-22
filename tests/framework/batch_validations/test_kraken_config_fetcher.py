"""
Kraken Config Fetcher — Cache Behavior Tests.

Unit tests for the runtime cache logic in KrakenConfigFetcher:

- An unreadable cache is set aside, never merged as empty; a write that fails leaves the previous
  cache whole.
- _merge_with_cache: existing symbols are not tombstoned; fresh symbols get
  _active:true and a _last_fetched timestamp.
- fetch_broker_config_with_cache: the age is asked of the REQUESTED symbol's own entry
  (#608) — a young file can hold an old entry, and the venue changes one symbol's minimum
  without touching the others. A symbol missing from the cache is fetched and merged; a forced
  refresh fetches whatever the age; a refresh that changes a minimum warns and says what changed.
- _build_symbol_config: Kraken's `costmin` is read as `cost_min`.

No real HTTP calls; all external I/O is patched or replaced with tmp_path files.
"""

import base64
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from python.configuration.autotrader.abstract_broker_config_fetcher import (
    AbstractBrokerConfigFetcher,
)
from python.configuration.autotrader.broker_config_cache_policy import get_entry_fetched_at
from python.configuration.autotrader.kraken_config_fetcher import (
    KrakenConfigFetcher,
    _merge_with_cache,
    _write_cache,
)
from python.framework.autotrader.startup.start_failure_classifier import is_passing_start_failure
from python.framework.exceptions.connection_errors import (
    ConnectionAttemptFailedError,
    ConnectionGaveUpError,
)
from python.framework.types.config_types.connection_policy_config_types import ConnectionPolicy
from python.framework.types.connection_types import GiveUpAction
from python.framework.utils.connection_ladder import ConnectionLadder
from tests.shared.recording_logger import RecordingLogger

_MODULE = 'python.configuration.autotrader.kraken_config_fetcher'

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
    fetcher._last_spec_changes = []
    fetcher._ladder = ConnectionLadder(
        'broker_boot_read', ConnectionPolicy(attempt_budget=1, on_give_up=GiveUpAction.ABORT),
        RecordingLogger())
    return fetcher


def _stamp(days_ago: float) -> str:
    """A `_last_fetched` stamp the given number of days before now."""
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime('%Y-%m-%dT%H:%M:%SZ')


def _cache_with(entries: dict, file_days_ago: float = 0.0) -> dict:
    """A cache dict whose FILE stamp is young unless told otherwise, holding the given entries."""
    return {
        '_config_meta': {'schema_version': '2.0', 'last_fetched': _stamp(file_days_ago),
                         'symbols_hash': 'abc12345'},
        'broker_info': {'company': 'Kraken'},
        'symbols': entries,
    }


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
# TestAnUnreadableCacheIsSetAside
# =============================================================================

class TestAnUnreadableCacheIsSetAside:
    """A cache that cannot be read keeps its symbols in a file of its own."""

    def test_an_unreadable_cache_is_set_aside_with_its_content(self, tmp_path):
        """A torn cache is moved aside and named in a warning; the new cache holds the fetch."""
        cache_path = tmp_path / 'kraken_spot_broker_config.json'
        torn = '{"symbols": {"ETHUSD": {"base_currency": "ETH", "volume_m'
        cache_path.write_text(torn)
        logger = RecordingLogger()
        fetcher = _make_fetcher()
        fetcher._logger = logger

        with (
            patch(f'{_MODULE}._get_runtime_cache_path', return_value=cache_path),
            patch.object(fetcher, 'fetch_broker_config',
                         return_value=_make_fresh_dict('DOTUSD', 'DOT', 'USD')),
        ):
            result = fetcher.fetch_broker_config_with_cache('DOTUSD', 'kraken_spot')

        aside = list(tmp_path.glob('kraken_spot_broker_config.json.unreadable-*'))
        assert len(aside) == 1, 'the torn cache is kept beside the new one'
        assert aside[0].read_text() == torn
        assert list(json.loads(cache_path.read_text())['symbols']) == ['DOTUSD']
        assert list(result['symbols']) == ['DOTUSD']
        assert aside[0].name in logger.text_at('warning'), 'the warning names where it went'

    def test_a_failed_write_leaves_the_previous_cache_whole(self, tmp_path):
        """A write that fails part-way never truncates the cache and leaves no temporary file."""
        cache_path = _make_cache_file(tmp_path, {'ETHUSD': {'base_currency': 'ETH'}})
        before = cache_path.read_text()

        with pytest.raises(TypeError):
            _write_cache({'symbols': {'DOTUSD': object()}}, cache_path)

        assert cache_path.read_text() == before, 'a failed write never truncates the cache'
        assert [p.name for p in tmp_path.iterdir()] == [cache_path.name], 'no temporary file left'


# =============================================================================
# TestFetchWithCacheSymbolCheck
# =============================================================================

class TestFetchWithCacheSymbolCheck:
    """fetch_broker_config_with_cache: the requested symbol's own age decides (#608)."""

    def _call(self, fetcher, cached, symbol, fresh=None, **kwargs):
        """Run the lookup over a given cache, with the API answering `fresh` when asked."""
        with (
            patch(f'{_MODULE}._load_cache_or_none', return_value=cached),
            patch(f'{_MODULE}._load_json', return_value=cached),
            patch(f'{_MODULE}._write_cache'),
            patch.object(fetcher, 'fetch_broker_config', return_value=fresh) as mock_fetch,
        ):
            result = fetcher.fetch_broker_config_with_cache(symbol, 'kraken_spot', **kwargs)
        return result, mock_fetch

    def test_fresh_cache_with_symbol_skips_api(self):
        """The symbol's entry is younger than the refresh interval → no API call made."""
        cached = _cache_with({'ETHUSD': {'base_currency': 'ETH', '_active': True,
                                         '_last_fetched': _stamp(1)}})
        result, mock_fetch = self._call(_make_fetcher(), cached, 'ETHUSD')

        mock_fetch.assert_not_called()
        assert 'ETHUSD' in result['symbols']

    def test_fresh_cache_missing_symbol_triggers_fetch(self):
        """Symbol absent from a fresh cache → API called, symbol merged into result."""
        cached = _cache_with({'ETHUSD': {'base_currency': 'ETH', 'quote_currency': 'USD',
                                         '_active': True, '_last_fetched': _stamp(1)}})
        result, mock_fetch = self._call(
            _make_fetcher(), cached, 'DOTUSD', _make_fresh_dict('DOTUSD', 'DOT', 'USD'))

        mock_fetch.assert_called_once_with('DOTUSD', 'kraken_spot')
        assert 'DOTUSD' in result['symbols'], 'DOTUSD must be in result after fetch.'
        assert 'ETHUSD' in result['symbols'], 'ETHUSD must be preserved (no tombstoning).'

    def test_a_young_file_with_an_old_entry_is_refreshed(self):
        """The defect of #608: another symbol's fetch renewed the FILE, this entry is 11 days old."""
        cached = _cache_with({'ETHUSD': {'base_currency': 'ETH', 'quote_currency': 'USD',
                                         'volume_min': 0.001, '_last_fetched': _stamp(11)}},
                             file_days_ago=0.5)
        _, mock_fetch = self._call(
            _make_fetcher(), cached, 'ETHUSD', _make_fresh_dict('ETHUSD', 'ETH', 'USD'))

        mock_fetch.assert_called_once_with('ETHUSD', 'kraken_spot')

    def test_an_entry_without_a_stamp_is_not_known_to_be_fresh(self):
        """No `_last_fetched` on the entry reads as stale, never as fresh."""
        cached = _cache_with({'ETHUSD': {'base_currency': 'ETH', 'quote_currency': 'USD'}})
        _, mock_fetch = self._call(
            _make_fetcher(), cached, 'ETHUSD', _make_fresh_dict('ETHUSD', 'ETH', 'USD'))

        mock_fetch.assert_called_once()

    def test_a_forced_refresh_fetches_a_fresh_entry(self):
        """`force_refresh` asks the venue whatever the entry's age — the sync's `--force`."""
        cached = _cache_with({'ETHUSD': {'base_currency': 'ETH', 'quote_currency': 'USD',
                                         '_last_fetched': _stamp(1)}})
        _, mock_fetch = self._call(
            _make_fetcher(), cached, 'ETHUSD', _make_fresh_dict('ETHUSD', 'ETH', 'USD'),
            force_refresh=True)

        mock_fetch.assert_called_once()

    def test_a_changed_minimum_warns_and_is_named(self):
        """A refresh moving `volume_min` warns once, in plain numbers; a field read for the
        first time (`cost_min` absent before) is not a change."""
        fetcher = _make_fetcher()
        fetcher._logger = MagicMock()
        cached = _cache_with({'BTCUSD': {'base_currency': 'BTC', 'quote_currency': 'USD',
                                         'volume_min': 5e-05, '_last_fetched': _stamp(30)}})
        fresh = _make_fresh_dict('BTCUSD', 'BTC', 'USD')
        fresh['symbols']['BTCUSD']['volume_min'] = 6e-05
        fresh['symbols']['BTCUSD']['cost_min'] = 0.5

        self._call(fetcher, cached, 'BTCUSD', fresh)

        assert fetcher.get_last_spec_changes() == ['volume_min 0.00005 → 0.00006']
        warning = fetcher._logger.warning.call_args.args[0]
        assert 'BTCUSD' in warning and 'sync --force' in warning and 'e-05' not in warning

    def test_an_unchanged_refresh_and_a_new_symbol_stay_quiet(self):
        """Nothing changed, or nothing was cached before → no change, no warning."""
        fetcher = _make_fetcher()
        fetcher._logger = MagicMock()
        cached = _cache_with({'ETHUSD': {'base_currency': 'ETH', 'quote_currency': 'USD',
                                         'volume_min': 0.001, '_last_fetched': _stamp(9)}})
        self._call(fetcher, cached, 'ETHUSD', _make_fresh_dict('ETHUSD', 'ETH', 'USD'))
        assert fetcher.get_last_spec_changes() == []

        self._call(fetcher, cached, 'DOTUSD', _make_fresh_dict('DOTUSD', 'DOT', 'USD'))
        assert fetcher.get_last_spec_changes() == []
        fetcher._logger.warning.assert_not_called()

    def test_a_failed_refresh_falls_back_to_the_entry_and_says_so(self):
        """The API is unreachable and the symbol is cached → the cache, with a warning naming it."""
        fetcher = _make_fetcher()
        fetcher._logger = MagicMock()
        cached = _cache_with({'ETHUSD': {'base_currency': 'ETH', 'quote_currency': 'USD',
                                         '_last_fetched': _stamp(40)}})
        with (
            patch(f'{_MODULE}._load_cache_or_none', return_value=cached),
            patch(f'{_MODULE}._load_json', return_value=cached),
            patch.object(fetcher, 'fetch_broker_config', side_effect=ConnectionError('down')),
        ):
            result = fetcher.fetch_broker_config_with_cache('ETHUSD', 'kraken_spot')

        assert result is cached
        warning = fetcher._logger.warning.call_args.args[0]
        assert 'ETHUSD' in warning and 'STALE' in warning

    def test_a_failed_fetch_of_an_uncached_symbol_is_a_hard_error(self):
        """The API is unreachable and the symbol was never cached → nothing to fall back to."""
        fetcher = _make_fetcher()
        cached = _cache_with({'ETHUSD': {'base_currency': 'ETH', '_last_fetched': _stamp(1)}})
        with (
            patch(f'{_MODULE}._load_cache_or_none', return_value=cached),
            patch.object(fetcher, 'fetch_broker_config', side_effect=ConnectionError('down')),
            pytest.raises(ConnectionError, match='NO ENTRY FOR DOTUSD'),
        ):
            fetcher.fetch_broker_config_with_cache('DOTUSD', 'kraken_spot')


class TestCostMinimum:
    """_build_symbol_config: Kraken's `costmin` becomes `cost_min` (#608)."""

    _PAIR = {'pair_decimals': 2, 'lot_decimals': 8, 'ordermin': '0.002', 'costmin': '0.5'}

    def test_costmin_is_read_beside_ordermin(self):
        entry = _make_fetcher()._build_symbol_config('ETHUSD', self._PAIR, 'ETH', 'USD', 'XETHZUSD')
        assert entry['volume_min'] == 0.002
        assert entry['cost_min'] == 0.5

    def test_a_pair_without_costmin_has_no_value_minimum(self):
        pair = {k: v for k, v in self._PAIR.items() if k != 'costmin'}
        entry = _make_fetcher()._build_symbol_config('ETHUSD', pair, 'ETH', 'USD', 'XETHZUSD')
        assert entry['cost_min'] == 0.0


class TestTheEntryStamp:
    """get_entry_fetched_at: whatever stands in `_last_fetched`, the answer is a UTC instant or None."""

    def test_the_written_form_is_read_as_utc(self):
        fetched_at = get_entry_fetched_at({'_last_fetched': '2026-10-18T12:54:22Z'})
        assert fetched_at == datetime(2026, 10, 18, 12, 54, 22, tzinfo=timezone.utc)

    def test_a_stamp_without_an_offset_is_read_as_utc(self):
        # A hand-edited stamp: the age arithmetic subtracts it from an aware now
        fetched_at = get_entry_fetched_at({'_last_fetched': '2026-10-18T12:54:22'})
        assert fetched_at == datetime(2026, 10, 18, 12, 54, 22, tzinfo=timezone.utc)
        assert fetched_at.tzinfo is timezone.utc

    @pytest.mark.parametrize('stamp', [1760792062, None, '', 'not a date'])
    def test_an_unreadable_stamp_is_no_stamp(self, stamp):
        assert get_entry_fetched_at({'_last_fetched': stamp}) is None


class TestFetchFeeTier:
    """
    fetch_fee_tier — ask the account which schedule it is actually on (#337).

    The rate written into a config file is a guess about which tier the account sits in.
    Measured 2026-09-08 against this project's own Kraken account: charged taker 0.8000 % /
    maker 0.4000 % while the config declared 0.40 / 0.25 — half of both, and in the direction
    that makes every maker/taker backtest look cheaper than it is.
    """

    _RESPONSE = {
        'currency': 'ZUSD',
        'volume': '267.01908',
        # Asked as ETHUSD, answered under Kraken's canonical name for the pair.
        'fees': {'XETHZUSD': {
            'fee': '0.8000', 'minfee': '0.0500', 'maxfee': '0.8000',
            'nextfee': '0.6000', 'nextvolume': '2500.0000',
        }},
        'fees_maker': {'XETHZUSD': {
            'fee': '0.4000', 'minfee': '0.0000', 'maxfee': '0.4000',
            'nextfee': '0.3000', 'nextvolume': '2500.0000',
        }},
    }

    def test_the_answer_is_read_although_it_is_keyed_by_the_VENUE_s_pair_name(self):
        """
        The trap this closes: `pair=ETHUSD` comes back under `XETHZUSD`, so indexing the
        response by the symbol raises KeyError. Measured, not assumed.
        """
        fetcher = _make_fetcher()

        with patch.object(fetcher, '_fetch_private', return_value=self._RESPONSE):
            tier = fetcher.fetch_fee_tier('ETHUSD')

        assert tier is not None
        assert tier.pair == 'XETHZUSD'
        assert tier.maker_pct == 0.4
        assert tier.taker_pct == 0.8

    def test_the_forward_looking_half_is_carried(self):
        """`next_*` is the only part that says when the cost drops — it is not decoration."""
        fetcher = _make_fetcher()

        with patch.object(fetcher, '_fetch_private', return_value=self._RESPONSE):
            tier = fetcher.fetch_fee_tier('ETHUSD')

        assert tier.next_maker_pct == 0.3
        assert tier.next_taker_pct == 0.6
        assert tier.next_volume_threshold == 2500.0
        assert tier.volume == 267.01908

    def test_a_transport_failure_is_never_fatal(self):
        """
        The session keeps its declared rates — which is exactly the state before this method
        existed, so a venue outage must not be able to stop a run.
        """
        fetcher = _make_fetcher()

        with patch.object(fetcher, '_fetch_private', side_effect=ConnectionError('down')):
            assert fetcher.fetch_fee_tier('ETHUSD') is None

    def test_an_unusable_answer_is_declined_rather_than_guessed(self):
        """
        More than one pair, or a block without a rate, means the shape is not the one that was
        measured — and inventing a number from it is worse than keeping the declared one.
        """
        fetcher = _make_fetcher()

        for raw in (
            {},
            {'fees': {}, 'fees_maker': {}},
            {'fees': {'A': {'fee': '0.8'}, 'B': {'fee': '0.4'}},
             'fees_maker': {'A': {'fee': '0.4'}}},
            {'fees': {'XETHZUSD': {'minfee': '0.05'}},
             'fees_maker': {'XETHZUSD': {'fee': '0.4'}}},
        ):
            with patch.object(fetcher, '_fetch_private', return_value=raw):
                assert fetcher.fetch_fee_tier('ETHUSD') is None, raw


class TestTheBalanceRead:
    """
    The boot's balance read (#598): a currency the account never held reads as zero, a passing fault
    is tried as often as the broker's budget allows, and the venue's own refusal ends the read at once.
    """

    @staticmethod
    def _answering(*answers):
        """A fetcher whose private reads return or raise the given answers, in order."""
        fetcher = _make_fetcher()
        fetcher._ladder = ConnectionLadder(
            'broker_boot_read',
            ConnectionPolicy(attempt_budget=3, initial_delay_s=0.0, max_delay_s=0.0,
                             on_give_up=GiveUpAction.ABORT),
            RecordingLogger())
        calls = []

        def fetch_private(endpoint, data=None):
            answer = answers[min(len(calls), len(answers) - 1)]
            calls.append(endpoint)
            if isinstance(answer, Exception):
                raise answer
            return answer

        fetcher._fetch_private = fetch_private
        return fetcher, calls

    def test_a_currency_the_account_never_held_reads_zero(self):
        fetcher, _ = self._answering({'ZUSD': '750.0000', 'XXBT': '0.0000000000'})
        assert fetcher.fetch_account_balance('USD') == 750.0
        assert fetcher.fetch_account_balance('ETH') == 0.0
        assert fetcher.fetch_account_balance('BTC') == 0.0

    def test_a_kraken_key_wins_over_the_code(self):
        fetcher, _ = self._answering({'XXRP': '12.5'})
        assert fetcher.fetch_account_balance('XRP') == 12.5

    def test_the_key_map_does_not_grow_with_each_read(self):
        fetcher, _ = self._answering({'XETH': '0.5'})
        fetcher.fetch_account_balance('ETH')
        fetcher.fetch_account_balance('ETH')
        assert KrakenConfigFetcher.STANDARD_TO_KRAKEN_BALANCE['ETH'] == ['XETH']

    def test_a_passing_fault_is_retried_within_the_budget(self):
        timeout = ConnectionAttemptFailedError('ReadTimeout on /0/private/Balance', terminal=False)
        fetcher, calls = self._answering(timeout, timeout, {'ZUSD': '750.0'})
        assert fetcher.fetch_account_balance('USD') == 750.0
        assert len(calls) == 3

    def test_an_exhausted_budget_raises_instead_of_reading_as_no_balance(self):
        timeout = ConnectionAttemptFailedError('ReadTimeout on /0/private/Balance', terminal=False)
        fetcher, calls = self._answering(timeout)
        with pytest.raises(ConnectionGaveUpError):
            fetcher.fetch_account_balance('USD')
        assert len(calls) == 3

    def test_the_venue_refusing_the_key_ends_the_read_at_once(self):
        fetcher, calls = self._answering(ConnectionError("Kraken API error: ['EAPI:Invalid key']"))
        with pytest.raises(ConnectionGaveUpError):
            fetcher.fetch_account_balance('USD')
        assert len(calls) == 1


class TestTheVenueSayingNotNow:
    """
    Kraken reports maintenance inside an answer, not as a status (#598): the balance read retries the
    venue's documented "not now" within its budget, and a give-up on it ends the start with exit 75.
    """

    @staticmethod
    def _posting(*bodies):
        """A fetcher whose private requests are answered with the given bodies, in order."""
        fetcher = _make_fetcher()
        fetcher._api_secret = base64.b64encode(b'test_secret').decode()
        fetcher._request_timeout_s = 1.0
        fetcher._ladder = ConnectionLadder(
            'broker_boot_read',
            ConnectionPolicy(attempt_budget=3, initial_delay_s=0.0, max_delay_s=0.0,
                             on_give_up=GiveUpAction.ABORT),
            RecordingLogger())
        posted = []

        def post(url, headers=None, data=None, timeout=None):
            body = bodies[min(len(posted), len(bodies) - 1)]
            posted.append(url)
            response = requests.Response()
            response.status_code = 200
            response._content = json.dumps(body).encode()
            return response

        return fetcher, posted, post

    def test_maintenance_is_waited_out_within_the_budget(self):
        fetcher, posted, post = self._posting({'error': ['EService:Unavailable'], 'result': {}},
                                              {'error': [], 'result': {'ZUSD': '10.0'}})
        with patch('python.configuration.autotrader.kraken_config_fetcher.requests.post', post):
            assert fetcher.fetch_account_balance('USD') == 10.0
        assert len(posted) == 2

    def test_maintenance_beyond_the_budget_is_a_passing_start_failure(self):
        fetcher, posted, post = self._posting({'error': ['EService:Unavailable'], 'result': {}})
        with patch('python.configuration.autotrader.kraken_config_fetcher.requests.post', post):
            with pytest.raises(ConnectionGaveUpError) as raised:
                fetcher.fetch_account_balance('USD')
        assert len(posted) == 3
        assert is_passing_start_failure(raised.value)

    def test_a_refused_key_is_not_waited_out(self):
        fetcher, posted, post = self._posting({'error': ['EAPI:Invalid key'], 'result': {}})
        with patch('python.configuration.autotrader.kraken_config_fetcher.requests.post', post):
            with pytest.raises(ConnectionGaveUpError) as raised:
                fetcher.fetch_account_balance('USD')
        assert len(posted) == 1
        assert not is_passing_start_failure(raised.value)


class _CachelessFetcher(AbstractBrokerConfigFetcher):
    """A venue fetcher with no runtime cache and no account tiers, so every default applies."""

    def __init__(self):
        self.fetches = 0

    def fetch_broker_config(self, symbol, broker_type):
        self.fetches += 1
        return {'symbols': {symbol: {}}}

    def fetch_account_balance(self, currency):
        return 0.0


class TestTheCapabilityIsByOverride:
    """A capability a venue does not have answers by default, and its caller needs no branch."""

    def test_the_abstract_default_declines(self):
        assert _CachelessFetcher().fetch_fee_tier('EURUSD') is None

    def test_without_a_cache_every_fetch_asks_the_venue(self):
        fetcher = _CachelessFetcher()
        fetcher.fetch_broker_config_with_cache('EURUSD', 'mt5')
        fetcher.fetch_broker_config_with_cache('EURUSD', 'mt5', force_refresh=True)
        assert fetcher.fetches == 2

    def test_without_a_cache_no_change_is_reported(self):
        fetcher = _CachelessFetcher()
        fetcher.fetch_broker_config_with_cache('EURUSD', 'mt5')
        assert fetcher.get_last_spec_changes() == []
