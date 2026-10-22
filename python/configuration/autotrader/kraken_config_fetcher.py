"""
FiniexTestingIDE - Kraken Config Fetcher
Fetches broker configuration and account balance from Kraken REST API.

Uses sync requests (startup-only, no async needed).
Adapts symbol mapping logic from DataCollector's broker_config_fetcher.py.
"""

import base64
import hashlib
import hmac
import json
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from python.configuration.autotrader.abstract_broker_config_fetcher import (
    AbstractBrokerConfigFetcher,
)
from python.configuration.autotrader.broker_config_cache_policy import (
    CACHE_REFRESH_DAYS,
    CACHE_STALE_WARN_DAYS,
    entry_age_days,
)
from python.configuration.credential_guard import assert_real_credential
from python.configuration.credentials_cascade import (
    resolve_credentials_file,
    tracked_credentials_path,
    workspace_credentials_path,
)
from python.configuration.data_home import resolve_in_data_home
from python.configuration.market_config_manager import MarketConfigManager
from python.framework.factory.broker_config_factory import BrokerConfigFactory
from python.framework.logging.bootstrap_logger import get_global_logger
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.trading_env.adapters.kraken_error_codes import raise_for_kraken_errors
from python.framework.types.config_types.connection_policy_config_types import ConnectionPolicy
from python.framework.types.connection_types import GiveUpAction
from python.framework.types.trading_env_types.broker_types import FeeTierInfo
from python.framework.utils.atomic_file_utils import write_text_atomic
from python.framework.utils.connection_ladder import ConnectionLadder, run_with_ladder
from python.framework.utils.http_attempt import send_classified

# Mirrors BrokerTransportConfig.request_timeout_s, which is where an operator tunes it.
# A constant here only for the direct-construction paths (the broker-config CLI) that
# have no broker entry in scope.
_DEFAULT_REQUEST_TIMEOUT_S: float = 15.0


class KrakenConfigFetcher(AbstractBrokerConfigFetcher):
    """
    Fetches broker config and account balance from Kraken REST API.

    Public endpoints: symbol specs via GET /0/public/AssetPairs.
    Private endpoints: account balance via POST /0/private/Balance (HMAC-SHA512 signed).

    Credentials are loaded via cascade: user_configs/credentials/ → configs/credentials/.

    Args:
        credentials_path: Credentials filename (resolved via cascade)
        logger: ScenarioLogger for status messages
    """

    API_BASE = 'https://api.kraken.com'

    # Kraken uses non-standard symbol names internally
    KRAKEN_TO_STANDARD = {
        'XBT': 'BTC',
        'XXBT': 'BTC',
        'XETH': 'ETH',
    }

    # Kraken's balance keys where they differ from the code a pair's wsname uses: X for the older
    # crypto assets, Z for fiat. Every other asset's key IS its code. Measured 2026-10-20 against
    # /0/public/Assets: 857 assets, 31 with a key that differs from their code — all of them below
    # but the fee credit KFEE. The map has to be complete, because an absent key reads as a zero
    # balance (fetch_account_balance).
    STANDARD_TO_KRAKEN_BALANCE = {
        'BTC': ['XXBT', 'XBT'],
        'ETH': ['XETH'],
        'ETC': ['XETC'],
        'LTC': ['XLTC'],
        'MLN': ['XMLN'],
        'REP': ['XREP'],
        'XDG': ['XXDG'],
        'XLM': ['XXLM'],
        'XMR': ['XXMR'],
        'XRP': ['XXRP'],
        'ZEC': ['XZEC'],
        'USD': ['ZUSD'],
        'EUR': ['ZEUR'],
        'GBP': ['ZGBP'],
        'CAD': ['ZCAD'],
        'JPY': ['ZJPY'],
        'AUD': ['ZAUD'],
        'ARS': ['ZARS'],
        'CLP': ['ZCLP'],
        'COP': ['ZCOP'],
        'DKK': ['ZDKK'],
        'GEL': ['ZGEL'],
        'GHS': ['ZGHS'],
        'LKR': ['ZLKR'],
        'MXN': ['ZMXN'],
        'PLN': ['ZPLN'],
        'SEK': ['ZSEK'],
        'UGX': ['ZUGX'],
        'VND': ['ZVND'],
        'XOF': ['ZXOF'],
    }

    def __init__(
        self,
        credentials_path: str,
        logger: Optional[ScenarioLogger] = None,
        api_base_url: Optional[str] = None,
        request_timeout_s: float = _DEFAULT_REQUEST_TIMEOUT_S,
        connection: Optional[ConnectionPolicy] = None,
    ):
        self._logger = logger
        self._api_base_url = api_base_url or self.API_BASE
        self._request_timeout_s = request_timeout_s
        # The broker's ladder, always ending in ABORT: what the boot reads here — the pairs, the
        # balance — has no degraded form to continue with (#598). Without a policy, one attempt.
        policy = connection or ConnectionPolicy(attempt_budget=1)
        self._ladder = ConnectionLadder(
            'broker_boot_read', policy.model_copy(update={'on_give_up': GiveUpAction.ABORT}),
            logger or get_global_logger())
        self._api_key, self._api_secret = self._load_credentials(credentials_path)
        self._last_spec_changes: List[str] = []

    def fetch_broker_config(self, symbol: str, broker_type: str) -> Dict[str, Any]:
        """
        Fetch symbol specs from Kraken AssetPairs endpoint.

        Filters to the requested symbol only. Builds a complete config dict
        compatible with KrakenAdapter / BrokerConfigFactory.from_serialized_dict().

        Args:
            symbol: Trading symbol (e.g., 'BTCUSD')
            broker_type: Broker type identifier (e.g., 'kraken_spot')

        Returns:
            Complete broker config dict
        """
        self._log_info(f'Fetching broker config for {symbol} from Kraken API...')

        pairs_data = self._fetch_public('/0/public/AssetPairs')
        symbol_config = self._find_symbol_in_pairs(symbol, pairs_data)

        if symbol_config is None:
            raise ValueError(
                f"Symbol '{symbol}' not found in Kraken AssetPairs response. "
                f"Check symbol name (expected format: BTCUSD, ETHUSD, etc.)"
            )

        config = self._build_full_config(symbol, symbol_config, broker_type)
        self._log_info(f'✅ Broker config fetched: {symbol}')
        return config

    def fetch_broker_config_with_cache(
        self, symbol: str, broker_type: str, force_refresh: bool = False
    ) -> Dict[str, Any]:
        """
        Fetch broker config with runtime cache and staleness guard.

        Lookup chain, asked of the REQUESTED symbol's own entry — a venue changes one symbol's
        minimum without touching the others, so the file's age says nothing about a symbol:
          1. Entry < 7 days old   → use cache silently, no API call
          2. Entry 7–30 days old  → try API refresh; on failure use cache + warning
          3. Entry > 30 days old  → try API refresh; on failure use cache + strong warning
          4. No entry, or no cache → fetch from API; failure = hard error with instructions
        A refresh that changes a minimum, a step or the tick size warns, naming both values.

        Cache location: data/runtime/brokers/<broker_type>/<broker_type>_broker_config.json

        Args:
            symbol: Trading symbol (e.g., 'BTCUSD')
            broker_type: Broker type identifier (e.g., 'kraken_spot')
            force_refresh: Fetch from the API whatever the entry's age

        Returns:
            Complete broker config dict (from cache or fresh API fetch)
        """
        cache_path = _get_runtime_cache_path(broker_type)
        cached = _load_cache_or_none(cache_path, self._logger)
        previous_entry = cached.get('symbols', {}).get(symbol) if cached else None
        entry_age = entry_age_days(previous_entry)
        self._last_spec_changes = []

        # Entry fresh (< 7 days): use the cache as it is
        if (not force_refresh and entry_age is not None
                and entry_age < CACHE_REFRESH_DAYS):
            self._log_info(
                f'🗄  Broker config from cache: {broker_type} {symbol} '
                f'({entry_age:.0f}d old, refresh in '
                f'{CACHE_REFRESH_DAYS - entry_age:.0f}d)'
            )
            return cached
        if cached is not None and previous_entry is None:
            self._log_info(
                f"🔄  Symbol '{symbol}' not in cache — fetching from API..."
            )

        # Stale, missing or forced: try API refresh — under the ladder only when nothing usable is
        # cached: a stale entry is a working fallback, and a ladder's give-up is logged as an error
        # that would grade the whole session (#598)
        try:
            if previous_entry is None:
                fresh_dict = run_with_ladder(
                    lambda: self.fetch_broker_config(symbol, broker_type), self._ladder)
            else:
                fresh_dict = self.fetch_broker_config(symbol, broker_type)
            merged = _merge_with_cache(fresh_dict, cache_path, self._logger)
            _write_cache(merged, cache_path)
            self._report_spec_changes(symbol, previous_entry, merged['symbols'][symbol])
            config_hash = merged.get('_config_meta', {}).get('symbols_hash', '')
            hash_tag = f' [symbols {config_hash}]' if config_hash else ''
            active_count = sum(1 for s in merged.get('symbols', {}).values() if s.get('_active', True))
            self._log_info(
                f'💱 Broker config refreshed: {broker_type} {symbol}{hash_tag} — {active_count} active symbols\n'
                f'    Cache: {cache_path}'
            )
            return merged
        except Exception as e:
            if previous_entry is not None:
                _warn_stale_cache(cache_path, symbol, entry_age, broker_type, e, self._logger)
                return cached

            # No entry for the symbol at all: hard error
            raise ConnectionError(
                f"❌ No broker config available for '{broker_type}' {symbol}.\n"
                f"\n"
                f"   Cache:   {cache_path} — "
                f"{'NO ENTRY FOR ' + symbol if cached is not None else 'NOT FOUND'}\n"
                f"   Reason:  {e}\n"
                f"\n"
                f"   This appears to be the symbol's first run, or the cache was deleted.\n"
                f"   Fix:     Ensure internet / API access and restart.\n"
                f"            Once fetched, the symbol is cached locally and only\n"
                f"            re-fetched when its entry is older than {CACHE_REFRESH_DAYS} days.\n"
                f"\n"
                f"   Offline fallback: Set \"config_mode\": \"static\" for '{broker_type}' in\n"
                f"            configs/market_config.json to use the static seed file instead:\n"
                f"            configs/brokers/kraken/kraken_spot_broker_config.json"
            ) from e

    def get_last_spec_changes(self) -> List[str]:
        """
        What the last refresh changed in the symbol's specification.

        Returns:
            One line per changed field (`volume_min 0.001 → 0.002`); empty when nothing
            changed, when the symbol was new, or when the last call read the cache
        """
        return list(self._last_spec_changes)

    def _report_spec_changes(
        self, symbol: str, previous: Optional[Dict[str, Any]], fresh: Dict[str, Any]
    ) -> None:
        """
        Warn when a refresh changed a field that decides whether an order is accepted.

        A field the previous entry did not carry is not a change: it was not read before,
        so nothing was decided on an old value of it.

        Args:
            symbol: The refreshed symbol
            previous: Its entry before the refresh (None when it was not cached)
            fresh: Its entry after the refresh

        Returns:
            None
        """
        if previous is None:
            return
        changes = [
            f'{field} {_spec_value(previous[field])} → {_spec_value(fresh.get(field))}'
            for field in _ORDER_DECIDING_FIELDS
            if field in previous and previous[field] != fresh.get(field)
        ]
        if not changes:
            return
        self._last_spec_changes = changes
        self._log_warning(
            f'⚠️ The venue changed {symbol}: {", ".join(changes)} '
            f'(last fetched {previous.get("_last_fetched", "unknown")}).\n'
            f'   A backtest prepared from a cache older than today used the old values — run\n'
            f'   `python python/cli/broker_config_cli.py sync --force` on that machine before comparing.'
        )

    def fetch_account_balance(self, currency: str) -> float:
        """
        The account's balance of one currency, read under the broker's connection ladder.

        Kraken lists every asset the account holds or once held, at zero where it is empty, and
        leaves out an asset the account never held — measured 2026-10-20: 19 of 23 listed assets
        at exactly zero, DOGE and XRP absent (#598). So a currency absent from the answer holds
        nothing, as long as the key looked up is Kraken's own (STANDARD_TO_KRAKEN_BALANCE).
        A read that cannot be completed raises ConnectionGaveUpError.

        Args:
            currency: Standard currency code (e.g., 'USD', 'BTC')

        Returns:
            The balance; 0.0 for a currency the account never held
        """
        self._log_info(f'Fetching account balance ({currency}) from Kraken API...')
        balance_data = run_with_ladder(
            lambda: self._fetch_private('/0/private/Balance'), self._ladder)

        # Kraken's key first (e.g., USD → ZUSD), then the code itself
        for key in [*self.STANDARD_TO_KRAKEN_BALANCE.get(currency, []), currency]:
            if key in balance_data:
                balance = float(balance_data[key])
                self._log_info(f'✅ Balance: {balance} {currency} (key={key})')
                return balance

        self._log_info(f'✅ Balance: 0.0 {currency} — not listed, so the account never held it')
        return 0.0

    def fetch_fee_tier(self, symbol: str) -> Optional[FeeTierInfo]:
        """
        Fetch the fee schedule this account is on for a symbol (#337).

        Kraken prices per account 30-day volume, so the rates in a config file are a guess
        about which tier the account sits in. Measured 2026-09-08 against this project's own
        account: charged taker 0.8000 % / maker 0.4000 % while the config declared 0.40 /
        0.25 — half of both, and the published eleven-tier table does not describe this pair
        at all (its entry level is 0.40/0.80 with the next step at a 30-day volume of 2500).
        That is why the rate is ASKED FOR rather than looked up.

        **The answer is keyed by the venue's CANONICAL pair name, not by the symbol asked
        about** — measured the same day: `pair=ETHUSD` comes back under `XETHZUSD`. Indexing
        the response by `symbol` raises KeyError, so the single entry per block is taken and
        its key is recorded as the pair.

        A failure is never fatal: the caller keeps its configured rates, which is the same
        state as before this method existed.

        Args:
            symbol: Trading symbol (e.g., 'ETHUSD')

        Returns:
            The account's current rates, or None when the venue did not answer usably
        """
        self._log_info(f'Fetching fee tier ({symbol}) from Kraken API...')

        try:
            raw = self._fetch_private('/0/private/TradeVolume', {'pair': symbol})
        except Exception as e:                                # noqa: BLE001 — reported below
            self._log_warning(
                f'Fee-tier fetch failed for {symbol}: {e} — keeping the configured rates')
            return None

        taker = self._single_fee_entry(raw.get('fees'))
        maker = self._single_fee_entry(raw.get('fees_maker'))
        if taker is None or maker is None:
            self._log_warning(
                f'Fee-tier response for {symbol} carried no usable rates '
                f'(keys: {sorted(raw.keys())}) — keeping the configured rates')
            return None

        pair, taker_block = taker
        _, maker_block = maker

        info = FeeTierInfo(
            maker_pct=float(maker_block['fee']),
            taker_pct=float(taker_block['fee']),
            next_maker_pct=self._optional_float(maker_block.get('nextfee')),
            next_taker_pct=self._optional_float(taker_block.get('nextfee')),
            next_volume_threshold=self._optional_float(taker_block.get('nextvolume')),
            volume=self._optional_float(raw.get('volume')),
            pair=pair,
        )
        self._log_info(
            f'✅ Fee tier for {symbol} ({info.pair}): '
            f'maker {info.maker_pct}% / taker {info.taker_pct}%'
        )
        return info

    @staticmethod
    def _single_fee_entry(block: Optional[Dict[str, Any]]) -> Optional[tuple]:
        """
        Take the one pair entry a TradeVolume fee block carries.

        Asked for one pair, Kraken answers with one entry — under ITS name for the pair, not
        the one asked about. Reading it positionally is therefore more correct than looking
        up the symbol, and it is what makes the canonical-name difference a non-issue.

        Args:
            block: The `fees` or `fees_maker` mapping from the response

        Returns:
            (pair name, entry), or None when the block is absent, empty or shaped otherwise
        """
        if not isinstance(block, dict) or len(block) != 1:
            return None
        pair, entry = next(iter(block.items()))
        if not isinstance(entry, dict) or 'fee' not in entry:
            return None
        return pair, entry

    @staticmethod
    def _optional_float(value: Any) -> Optional[float]:
        """
        Read a numeric field the venue may omit.

        Args:
            value: The raw field, possibly absent or an empty string

        Returns:
            The value as a float, or None when it is absent or unreadable
        """
        if value in (None, ''):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    # =========================================================================
    # HTTP HELPERS
    # =========================================================================

    def _fetch_public(self, endpoint: str, params: Optional[Dict] = None) -> Dict[str, Any]:
        """
        GET request to Kraken public API.

        Args:
            endpoint: API path (e.g., '/0/public/AssetPairs')
            params: Optional query parameters

        Returns:
            API result dict
        """
        url = f'{self._api_base_url}{endpoint}'
        response = send_classified(
            lambda: requests.get(url, params=params, timeout=self._request_timeout_s), endpoint)

        data = response.json()
        # The venue's own "not now" goes to the ladder as transient (#598); any other error is
        # its answer
        raise_for_kraken_errors(data.get('error', []))

        return data.get('result', {})

    def _fetch_private(self, endpoint: str, data: Optional[Dict] = None) -> Dict[str, Any]:
        """
        POST request to Kraken private API with HMAC-SHA512 signing.

        Args:
            endpoint: API path (e.g., '/0/private/Balance')
            data: Optional POST data

        Returns:
            API result dict
        """
        if data is None:
            data = {}

        headers = self._sign_request(endpoint, data)
        url = f'{self._api_base_url}{endpoint}'

        response = send_classified(
            lambda: requests.post(url, headers=headers, data=data, timeout=self._request_timeout_s),
            endpoint)

        result = response.json()
        # The venue's own "not now" goes to the ladder as transient (#598); any other error is
        # its answer
        raise_for_kraken_errors(result.get('error', []))

        return result.get('result', {})

    def _sign_request(self, url_path: str, data: Dict) -> Dict[str, str]:
        """
        Create HMAC-SHA512 signed headers for private API request.

        Args:
            url_path: API endpoint path (e.g., '/0/private/Balance')
            data: POST data dict (nonce is added automatically)

        Returns:
            Headers dict with API-Key and API-Sign
        """
        nonce = str(int(time.time() * 1000))
        data['nonce'] = nonce

        post_data = urllib.parse.urlencode(data)
        encoded = (nonce + post_data).encode()
        message = url_path.encode() + hashlib.sha256(encoded).digest()

        signature = hmac.new(
            base64.b64decode(self._api_secret),
            message,
            hashlib.sha512,
        )

        return {
            'API-Key': self._api_key,
            'API-Sign': base64.b64encode(signature.digest()).decode(),
        }

    # =========================================================================
    # SYMBOL MATCHING & CONFIG BUILDING
    # =========================================================================

    def _find_symbol_in_pairs(
        self,
        target_symbol: str,
        pairs_data: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """
        Find and extract pair info for target symbol from AssetPairs response.

        Matches using wsname field after normalization (XBT→BTC).

        Args:
            target_symbol: Standard symbol (e.g., 'BTCUSD')
            pairs_data: Raw AssetPairs response data

        Returns:
            Symbol config dict, or None if not found
        """
        # Split target into base/quote (e.g., 'BTCUSD' → 'BTC', 'USD')
        target_base, target_quote = self._split_symbol(target_symbol)

        for _pair_name, pair_info in pairs_data.items():
            wsname = pair_info.get('wsname', '')
            if '/' not in wsname:
                continue

            api_base, api_quote = wsname.split('/')
            api_base = self.KRAKEN_TO_STANDARD.get(api_base, api_base)

            if api_base == target_base and api_quote == target_quote:
                return self._build_symbol_config(target_symbol, pair_info, api_base, api_quote, _pair_name)

        return None

    def _build_symbol_config(
        self,
        symbol: str,
        pair_info: Dict[str, Any],
        base_currency: str,
        quote_currency: str,
        kraken_pair_name: str = '',
    ) -> Dict[str, Any]:
        """
        Build symbol config entry from API pair info.

        Args:
            symbol: Standard symbol (e.g., 'BTCUSD')
            pair_info: Raw API pair data
            base_currency: Normalized base currency from API wsname (e.g., 'BTC')
            quote_currency: Quote currency from API wsname (e.g., 'USD')
            kraken_pair_name: Kraken internal pair name (e.g., 'XXBTZUSD') for order API calls

        Returns:
            Symbol config dict matching static JSON structure
        """
        pair_decimals = pair_info.get('pair_decimals', 1)
        lot_decimals = pair_info.get('lot_decimals', 8)
        ordermin = float(pair_info.get('ordermin', 0.0001))
        # The smallest order VALUE, in the quote currency — a second minimum beside ordermin
        costmin = float(pair_info.get('costmin', 0.0))

        tick_size = 10 ** (-pair_decimals)

        return {
            'description': f'{base_currency} vs {quote_currency}',
            'base_currency': base_currency,
            'quote_currency': quote_currency,
            'trade_allowed': True,
            'volume_min': ordermin,
            'cost_min': costmin,
            'volume_max': 10000.0,
            'volume_step': 10 ** (-lot_decimals),
            'volume_limit': 0.0,
            'contract_size': 1.0,
            'tick_size': tick_size,
            'point': tick_size,
            'digits': pair_decimals,
            'spread_float': False,
            'stops_level': 0,
            'freeze_level': 0,
            'kraken_pair_name': kraken_pair_name,
        }

    def _build_full_config(
        self,
        symbol: str,
        symbol_config: Dict[str, Any],
        broker_type: str,
    ) -> Dict[str, Any]:
        """
        Build complete broker config dict from fetched symbol data.

        The fee structure is READ from the broker's git-tracked seed, never written here
        (#337). It used to be a literal in this method, which made three places declare the
        same rate — the seed, this code and the cache file it writes — and a re-freeze of the
        seed could not reach two of them. One declaration, everything else points at it.

        Args:
            symbol: Standard symbol (e.g., 'BTCUSD')
            symbol_config: Symbol config from _build_symbol_config()
            broker_type: Broker type (e.g., 'kraken_spot')

        Returns:
            Complete config dict compatible with KrakenAdapter
        """
        now = datetime.now(timezone.utc)

        return {
            '_comment': 'Live broker config fetched from Kraken API',
            '_version': '1.1',
            'broker_type': broker_type,
            # No symbol count here — this builds one symbol, the cache merge holds
            # many, and len(symbols) beside it answers that without going stale.
            'export_info': {
                'timestamp': now.isoformat(),
                'source': 'Kraken REST API (live fetch)',
                'exporter_version': '1.01',
            },
            'broker_info': {
                'company': 'Kraken',
                'server': broker_type,
                'name': 'kraken_live',
                'trade_mode': 'live',
                'leverage': 1,
                'hedging_allowed': False,
            },
            'fee_structure': BrokerConfigFactory.fee_structure_from(
                MarketConfigManager().get_broker_config_path(broker_type)),
            # No 'order_types' here: an adapter's get_order_capabilities() is the one
            # declaration of what a venue accepts, and a second list in the fetched config
            # was read by nobody while disagreeing with it (#500).
            'trading_permissions': {
                'trade_allowed': True,
                'limit_orders': 1000,
            },
            'symbols': {
                symbol: symbol_config,
            },
        }

    # =========================================================================
    # UTILITIES
    # =========================================================================

    def _split_symbol(self, symbol: str) -> tuple:
        """
        Split standard symbol into base and quote currency.

        Handles common crypto pairs by known quote currencies.

        Args:
            symbol: Standard symbol (e.g., 'BTCUSD', 'ETHEUR')

        Returns:
            (base, quote) tuple
        """
        known_quotes = ['USD', 'EUR', 'GBP', 'CAD', 'JPY', 'AUD']
        for quote in known_quotes:
            if symbol.endswith(quote):
                base = symbol[:-len(quote)]
                return base, quote

        # Fallback: assume last 3 chars are quote
        return symbol[:-3], symbol[-3:]

    @staticmethod
    def _load_credentials(credentials_filename: str) -> tuple:
        """
        Load API credentials via cascade: user_configs/credentials/ → configs/credentials/.

        Args:
            credentials_filename: Credentials filename (e.g., 'venues/kraken_credentials.json')

        Returns:
            (api_key, api_secret) tuple
        """
        # The cascade is the framework's, so a venue package carries no copy of it (#328);
        # what this venue adds is which keys it reads from the file.
        cred_path = resolve_credentials_file(credentials_filename)
        if cred_path is None:
            raise FileNotFoundError(
                f"Credentials file not found. Expected at:\n"
                f"  {workspace_credentials_path(credentials_filename)} (user override)\n"
                f"  {tracked_credentials_path(credentials_filename)} (default)\n"
                f"Create one with {{'api_key': '...', 'api_secret': '...'}}"
            )

        assert_real_credential(cred_path, 'Fetching the Kraken broker config')

        with open(cred_path, 'r') as f:
            creds = json.load(f)

        api_key = creds.get('api_key', '')
        api_secret = creds.get('api_secret', '')

        if not api_key or not api_secret:
            raise ValueError(
                f"Credentials file missing 'api_key' or 'api_secret': {cred_path}"
            )

        return api_key, api_secret

    def _log_info(self, message: str) -> None:
        """Log info message if logger available."""
        if self._logger:
            self._logger.info(message)

    def _log_warning(self, message: str) -> None:
        """Log warning message if logger available."""
        if self._logger:
            self._logger.warning(message)


# =============================================================================
# RUNTIME CACHE HELPERS  (module-level, used by KrakenConfigFetcher)
# =============================================================================

# Public because the store catalog (#486) registers this directory as a store and must name it
# from its one definition rather than repeating the literal. Relative to the data home (#598).
RUNTIME_CACHE_BASE = Path('data/runtime/brokers')
# The fields whose change can turn an accepted order into a refused one, or the reverse
_ORDER_DECIDING_FIELDS = ('volume_min', 'volume_step', 'tick_size', 'cost_min')


def _get_runtime_cache_path(broker_type: str) -> Path:
    """Return the runtime cache file path for a broker type."""
    return resolve_in_data_home(RUNTIME_CACHE_BASE) / broker_type / f'{broker_type}_broker_config.json'


def get_runtime_cache_path(broker_type: str) -> Path:
    """
    Return the runtime cache file path for a broker type.

    Args:
        broker_type: Broker type identifier (e.g., 'kraken_spot')

    Returns:
        Path to the runtime cache JSON file
    """
    return _get_runtime_cache_path(broker_type)


def load_runtime_cache(broker_type: str) -> Dict[str, Any]:
    """
    Load the runtime cache for a broker type without any API calls.

    Args:
        broker_type: Broker type identifier (e.g., 'kraken_spot')

    Returns:
        Cache dict loaded from the runtime cache file

    Raises:
        FileNotFoundError: If no runtime cache exists for the broker
    """
    cache_path = _get_runtime_cache_path(broker_type)
    if not cache_path.exists():
        raise FileNotFoundError(
            f"❌ No runtime cache found for '{broker_type}'.\n"
            f"   Cache:  {cache_path}\n"
            f"   Fix:    Start an AutoTrader session for any {broker_type} symbol (once)\n"
            f"           to populate the cache, or run:\n"
            f"           python python/cli/broker_config_cli.py sync --broker {broker_type}"
        )
    data = _load_json(cache_path)
    # Inject broker_type if absent — caches written before this field was added
    if 'broker_type' not in data:
        data['broker_type'] = broker_type
    return data


def _spec_value(value: Any) -> str:
    """
    A specification value as an operator reads it — never in scientific notation.

    Args:
        value: The value from a cache entry

    Returns:
        `0.00005` rather than `5e-05`; anything that is not a float as it is
    """
    if not isinstance(value, float):
        return str(value)
    return f'{value:.10f}'.rstrip('0').rstrip('.') or '0'


def _load_cache_or_none(
    cache_path: Path, logger: Optional[ScenarioLogger] = None
) -> Optional[Dict[str, Any]]:
    """
    Load the runtime cache, or None when it is missing or cannot be read. A cache that cannot be
    read is set aside first, so the refresh that follows cannot replace the symbols it still holds
    with the one it fetched.

    Args:
        cache_path: Path to runtime cache file
        logger: ScenarioLogger for the warning when the cache is set aside (may be None)

    Returns:
        The cache dict, or None
    """
    if not cache_path.exists():
        return None
    try:
        cached = _load_json(cache_path)
        if not isinstance(cached, dict):
            raise ValueError(f'a JSON {type(cached).__name__}, not an object')
        return cached
    except (OSError, ValueError) as error:
        _set_aside_unreadable_cache(cache_path, error, logger)
        return None


def _load_json(path: Path) -> Dict[str, Any]:
    """Load JSON file and return as dict."""
    with open(path, 'r') as f:
        return json.load(f)


def _write_cache(config_dict: Dict[str, Any], cache_path: Path) -> None:
    """
    Write broker config dict to runtime cache file — atomically, so a session killed while writing
    leaves the previous file whole instead of a torn one.

    Args:
        config_dict: Broker config dict to write
        cache_path: Destination path
    """
    write_text_atomic(cache_path, json.dumps(config_dict, indent=2))


def _set_aside_unreadable_cache(
    cache_path: Path, error: Exception, logger: Optional[ScenarioLogger]
) -> None:
    """
    Move a runtime cache that cannot be read out of the way, and say where it went.

    A torn write or a hand edit leaves such a file. Merged as empty, it would lose every symbol it
    still holds; set aside, it stays for a look, and each of its symbols is fetched again when a
    session first asks for it.

    Args:
        cache_path: The unreadable cache file
        error: What reading it raised
        logger: ScenarioLogger for the warning; the global log when None
    """
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    aside = cache_path.with_name(f'{cache_path.name}.unreadable-{stamp}')
    try:
        cache_path.replace(aside)
        outcome = (f'set aside as {aside.name}. Its symbols are fetched again when a session first '
                   f'asks for one; look at the set-aside file, then delete it.')
    except OSError as move_error:
        outcome = (f'and could not be set aside ({move_error}); the next refresh replaces it with '
                   f'the symbol it fetched.')
    (logger or get_global_logger()).warning(
        f'⚠️  The broker cache {cache_path} could not be read ({error}) — {outcome}')


def _merge_with_cache(
    fresh_dict: Dict[str, Any], cache_path: Path, logger: Optional[ScenarioLogger] = None
) -> Dict[str, Any]:
    """
    Extend existing cache with freshly fetched symbol(s).

    Since each fetch covers only one symbol, existing symbols are kept as-is —
    they are not tombstoned. Tombstoning (setting _active: false) must be done
    by a full-refresh path (fetch all symbols) which is not yet implemented.

    Merge rules:
      - Symbol in fresh             → add or update, set _active: true, set _last_fetched: now
      - Symbol in cache only        → keep unchanged (_active and _last_fetched preserved)

    Also updates _config_meta with current timestamp and recomputed hash.

    Args:
        fresh_dict: Config dict from broker API (typically one symbol)
        cache_path: Path to existing cache (may not exist)
        logger: ScenarioLogger for the warning when the cache is set aside (may be None)

    Returns:
        Merged config dict with updated _config_meta
    """
    merged_symbols: Dict[str, Any] = {}

    # Load existing cache symbols if present — keep their current state. No exists() check:
    # callers mock _load_json directly, and a check would make the function depend on the
    # filesystem anyway. A cache that cannot be read is set aside rather than merged as empty —
    # merged as empty, the write that follows would drop every symbol it still holds.
    existing: Any = None
    try:
        existing = _load_json(cache_path)
    except FileNotFoundError:
        pass  # no cache yet: start fresh
    except (OSError, ValueError) as error:
        _set_aside_unreadable_cache(cache_path, error, logger)
    if isinstance(existing, dict):
        for sym, spec in existing.get('symbols', {}).items():
            merged_symbols[sym] = dict(spec)

    # Add / update fresh API symbols (always active)
    now_str = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    for sym, spec in fresh_dict.get('symbols', {}).items():
        merged_symbols[sym] = dict(spec)
        merged_symbols[sym]['_active'] = True
        merged_symbols[sym]['_last_fetched'] = now_str

    # Build merged dict from fresh structure (preserves broker_info, fee_structure, etc.)
    result = dict(fresh_dict)
    result['symbols'] = merged_symbols

    # Update _config_meta
    symbols_hash = BrokerConfigFactory.compute_symbols_hash(merged_symbols)
    result['_config_meta'] = {
        'schema_version': '2.0',
        'last_fetched': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'symbols_hash': symbols_hash,
    }

    return result


def _warn_stale_cache(
    cache_path: Path,
    symbol: str,
    age_days: Optional[float],
    broker_type: str,
    error: Exception,
    logger: Optional[ScenarioLogger],
) -> None:
    """
    Log a staleness warning for one symbol's entry, with human-readable instructions.

    Args:
        cache_path: Path to the cache file
        symbol: The symbol whose entry could not be refreshed
        age_days: Age of the symbol's entry in days (None: it carries no readable stamp)
        broker_type: Broker type identifier
        error: Exception from the failed API refresh attempt
        logger: ScenarioLogger for status messages (may be None)
    """
    is_very_stale = age_days is None or age_days > CACHE_STALE_WARN_DAYS

    try:
        cached_data = _load_json(cache_path)
        config_hash = cached_data.get('_config_meta', {}).get('symbols_hash', '')
        symbols = cached_data.get('symbols', {})
        active_count = sum(1 for s in symbols.values() if s.get('_active', True))
        hash_tag = f' [{config_hash}]' if config_hash else ''
        symbol_summary = f'{active_count} active symbols{hash_tag}'
    except Exception:
        symbol_summary = 'cache unreadable'

    try:
        last_fetched = cached_data['symbols'][symbol].get('_last_fetched', 'unknown')  # type: ignore[index]
    except Exception:
        last_fetched = 'unknown'

    age_label = f'{age_days:.0f} days ago' if age_days is not None else 'age unknown'
    age_old = f'{age_days:.0f} days old' if age_days is not None else 'of unknown age'

    if is_very_stale:
        msg = (
            f'⚠️  {symbol} broker config is {age_old} — its specification may be outdated.\n'
            f'    Cache:   {cache_path}\n'
            f'    Cached:  {last_fetched} ({age_label}) — STALE (>{CACHE_STALE_WARN_DAYS} days)\n'
            f'    Config:  {symbol_summary}\n'
            f'    Reason:  {error}\n'
            f'\n'
            f'    Risk:    Volume limits, tick sizes, or listed symbols may have changed.\n'
            f'             Delisted symbols will continue trading on outdated specs.\n'
            f'    Fix:     Ensure internet / API access and restart to force a refresh.\n'
            f'\n'
            f'    Static seed (for manual inspection and git-tracked reference):\n'
            f'    configs/brokers/kraken/kraken_spot_broker_config.json\n'
            f'    (sync and commit after a successful cache refresh)'
        )
    else:
        msg = (
            f'⚠️  Could not refresh the {symbol} broker config — using cached version.\n'
            f'    Cache:   {cache_path}\n'
            f'    Cached:  {last_fetched} ({age_label}) — within acceptable range\n'
            f'    Config:  {symbol_summary}\n'
            f'    Reason:  {error}\n'
            f'    Next:    A fresh fetch will be attempted on your next session start.\n'
            f'             To force a refresh: ensure API access and restart.'
        )

    if logger:
        logger.warning(msg)
    else:
        print(msg)
