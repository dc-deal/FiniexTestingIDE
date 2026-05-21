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
from typing import Any, Dict, Optional

import requests

from python.configuration.autotrader.abstract_broker_config_fetcher import AbstractBrokerConfigFetcher
from python.framework.logging.scenario_logger import ScenarioLogger


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
    REQUEST_TIMEOUT_S = 15

    # Kraken uses non-standard symbol names internally
    KRAKEN_TO_STANDARD = {
        'XBT': 'BTC',
        'XXBT': 'BTC',
        'XETH': 'ETH',
    }

    # Kraken prefixes: X for crypto, Z for fiat
    STANDARD_TO_KRAKEN_BALANCE = {
        'BTC': ['XXBT', 'XBT'],
        'ETH': ['XETH'],
        'USD': ['ZUSD'],
        'EUR': ['ZEUR'],
        'GBP': ['ZGBP'],
        'CAD': ['ZCAD'],
        'JPY': ['ZJPY'],
        'AUD': ['ZAUD'],
    }

    def __init__(
        self,
        credentials_path: str,
        logger: Optional[ScenarioLogger] = None,
        api_base_url: Optional[str] = None,
    ):
        self._logger = logger
        self._api_base_url = api_base_url or self.API_BASE
        self._api_key, self._api_secret = self._load_credentials(credentials_path)

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
        self._log_info(f"Fetching broker config for {symbol} from Kraken API...")

        pairs_data = self._fetch_public('/0/public/AssetPairs')
        symbol_config = self._find_symbol_in_pairs(symbol, pairs_data)

        if symbol_config is None:
            raise ValueError(
                f"Symbol '{symbol}' not found in Kraken AssetPairs response. "
                f"Check symbol name (expected format: BTCUSD, ETHUSD, etc.)"
            )

        config = self._build_full_config(symbol, symbol_config, broker_type)
        self._log_info(f"✅ Broker config fetched: {symbol}")
        return config

    def fetch_broker_config_with_cache(self, symbol: str, broker_type: str) -> Dict[str, Any]:
        """
        Fetch broker config with runtime cache and staleness guard.

        Lookup chain:
          1. Cache < 7 days old   → use cache silently, no API call
          2. Cache 7–30 days old  → try API refresh; on failure use cache + warning
          3. Cache > 30 days old  → try API refresh; on failure use cache + strong warning
          4. No cache present     → fetch from API; failure = hard error with instructions

        Cache location: data/runtime/brokers/<broker_type>/<broker_type>_broker_config.json

        Args:
            symbol: Trading symbol (e.g., 'BTCUSD')
            broker_type: Broker type identifier (e.g., 'kraken_spot')

        Returns:
            Complete broker config dict (from cache or fresh API fetch)
        """
        cache_path = _get_runtime_cache_path(broker_type)
        cache_age_days = _get_cache_age_days(cache_path)

        # Cache fresh (< 7 days): use as-is if requested symbol is present
        if cache_age_days is not None and cache_age_days < _CACHE_REFRESH_DAYS:
            cached = _load_json(cache_path)
            if symbol in cached.get('symbols', {}):
                self._log_info(
                    f"🗄  Broker config from cache: {broker_type} "
                    f"({cache_age_days:.0f}d old, refresh in "
                    f"{_CACHE_REFRESH_DAYS - cache_age_days:.0f}d)"
                )
                return cached
            # Symbol not in cache yet — fall through to fetch and extend
            self._log_info(
                f"🔄  Symbol '{symbol}' not in cache — fetching from API..."
            )

        # Stale or no cache: try API refresh
        try:
            fresh_dict = self.fetch_broker_config(symbol, broker_type)
            merged = _merge_with_cache(fresh_dict, cache_path)
            _write_cache(merged, cache_path)
            config_hash = merged.get('_config_meta', {}).get('symbols_hash', '')
            hash_tag = f' [{config_hash}]' if config_hash else ''
            active_count = sum(1 for s in merged.get('symbols', {}).values() if s.get('_active', True))
            self._log_info(
                f"💱 Broker config refreshed: {broker_type}{hash_tag} — {active_count} active symbols\n"
                f"    Cache: {cache_path}"
            )
            return merged
        except Exception as e:
            if cache_age_days is not None:
                _warn_stale_cache(cache_path, cache_age_days, broker_type, e, self._logger)
                return _load_json(cache_path)

            # No cache at all: hard error
            raise ConnectionError(
                f"❌ No broker config available for '{broker_type}'.\n"
                f"\n"
                f"   Cache:   {cache_path} — NOT FOUND\n"
                f"   Reason:  {e}\n"
                f"\n"
                f"   This appears to be your first run, or the cache was deleted.\n"
                f"   Fix:     Ensure internet / API access and restart.\n"
                f"            Once fetched, the config is cached locally and only\n"
                f"            re-fetched when it is older than {_CACHE_REFRESH_DAYS} days.\n"
                f"\n"
                f"   Offline fallback: Set \"config_mode\": \"static\" for '{broker_type}' in\n"
                f"            configs/market_config.json to use the static seed file instead:\n"
                f"            configs/brokers/kraken/kraken_spot_broker_config.json"
            ) from e

    def fetch_account_balance(self, currency: str) -> Optional[float]:
        """
        Fetch account balance from Kraken Balance endpoint.

        Args:
            currency: Standard currency code (e.g., 'USD', 'BTC')

        Returns:
            Balance amount, or None if currency not found in account
        """
        self._log_info(f"Fetching account balance ({currency}) from Kraken API...")

        try:
            balance_data = self._fetch_private('/0/private/Balance')
        except Exception as e:
            self._log_warning(f"Balance fetch failed: {e}")
            return None

        # Try Kraken-specific key variants (e.g., USD → ZUSD)
        keys_to_try = self.STANDARD_TO_KRAKEN_BALANCE.get(currency, [])
        keys_to_try.append(currency)

        for key in keys_to_try:
            if key in balance_data:
                balance = float(balance_data[key])
                self._log_info(f"✅ Balance: {balance} {currency} (key={key})")
                return balance

        self._log_warning(
            f"Currency '{currency}' not found in balance response. "
            f"Available: {list(balance_data.keys())}"
        )
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
        url = f"{self._api_base_url}{endpoint}"
        response = requests.get(url, params=params, timeout=self.REQUEST_TIMEOUT_S)
        response.raise_for_status()

        data = response.json()
        errors = data.get('error', [])
        if errors:
            raise ConnectionError(f"Kraken API error: {errors}")

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
        url = f"{self._api_base_url}{endpoint}"

        response = requests.post(
            url,
            headers=headers,
            data=data,
            timeout=self.REQUEST_TIMEOUT_S,
        )
        response.raise_for_status()

        result = response.json()
        errors = result.get('error', [])
        if errors:
            raise ConnectionError(f"Kraken API error: {errors}")

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

        tick_size = 10 ** (-pair_decimals)

        return {
            'description': f"{base_currency} vs {quote_currency}",
            'base_currency': base_currency,
            'quote_currency': quote_currency,
            'trade_allowed': True,
            'volume_min': ordermin,
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

        Uses hardcoded fee structure (maker 0.25%, taker 0.40%) — Kraken
        Tier-0 published rates. Account-tier auto-detection via
        /0/private/TradeVolume is tracked in a separate issue.

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
            'export_info': {
                'timestamp': now.isoformat(),
                'source': 'Kraken REST API (live fetch)',
                'exporter_version': '1.01',
                'symbols_total': 1,
            },
            'broker_info': {
                'company': 'Kraken',
                'server': broker_type,
                'name': 'kraken_live',
                'trade_mode': 'live',
                'leverage': 1,
                'hedging_allowed': False,
            },
            'fee_structure': {
                'model': 'maker_taker',
                'maker_fee': 0.25,
                'taker_fee': 0.40,
                'fee_currency': 'quote',
            },
            'trading_permissions': {
                'trade_allowed': True,
                'limit_orders': 1000,
                'order_types': {
                    'market': True,
                    'limit': True,
                    'stop': True,
                    'stop_limit': True,
                },
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
            credentials_filename: Credentials filename (e.g., 'kraken_credentials.json')

        Returns:
            (api_key, api_secret) tuple
        """
        user_path = Path('user_configs/credentials') / credentials_filename
        default_path = Path('configs/credentials') / credentials_filename

        # Cascade: user override first, then tracked default
        if user_path.exists():
            cred_path = user_path
        elif default_path.exists():
            cred_path = default_path
        else:
            raise FileNotFoundError(
                f"Credentials file not found. Expected at:\n"
                f"  {user_path} (user override)\n"
                f"  {default_path} (default)\n"
                f"Create one with {{'api_key': '...', 'api_secret': '...'}}"
            )

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

_RUNTIME_CACHE_BASE = Path('data/runtime/brokers')
_CACHE_REFRESH_DAYS = 7
_CACHE_STALE_WARN_DAYS = 30


def _get_runtime_cache_path(broker_type: str) -> Path:
    """Return the runtime cache file path for a broker type."""
    return _RUNTIME_CACHE_BASE / broker_type / f'{broker_type}_broker_config.json'


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


def _get_cache_age_days(cache_path: Path) -> Optional[float]:
    """
    Return age of cache file in days, or None if file does not exist.

    Args:
        cache_path: Path to runtime cache file

    Returns:
        Age in days, or None if cache missing
    """
    if not cache_path.exists():
        return None
    try:
        with open(cache_path, 'r') as f:
            data = json.load(f)
        last_fetched_str = data.get('_config_meta', {}).get('last_fetched')
        if not last_fetched_str:
            return None
        last_fetched = datetime.fromisoformat(last_fetched_str.replace('Z', '+00:00'))
        age = (datetime.now(timezone.utc) - last_fetched).total_seconds() / 86400.0
        return age
    except Exception:
        return None


def _load_json(path: Path) -> Dict[str, Any]:
    """Load JSON file and return as dict."""
    with open(path, 'r') as f:
        return json.load(f)


def _write_cache(config_dict: Dict[str, Any], cache_path: Path) -> None:
    """
    Write broker config dict to runtime cache file.

    Args:
        config_dict: Broker config dict to write
        cache_path: Destination path
    """
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_path, 'w') as f:
        json.dump(config_dict, f, indent=2)


def _compute_symbols_hash(symbols: Dict[str, Any]) -> str:
    """
    Compute 8-char SHA256 hash of the symbols block.

    Hash is stable across _config_meta changes — only symbols data affects it.

    Args:
        symbols: The 'symbols' dict from a broker config

    Returns:
        8-character hex hash string
    """
    return hashlib.sha256(
        json.dumps(symbols, sort_keys=True).encode()
    ).hexdigest()[:8]


def _merge_with_cache(fresh_dict: Dict[str, Any], cache_path: Path) -> Dict[str, Any]:
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

    Returns:
        Merged config dict with updated _config_meta
    """
    merged_symbols: Dict[str, Any] = {}

    # Load existing cache symbols if present — keep their current state.
    # The try/except handles both missing files and corrupted JSON in one
    # branch; an explicit exists() check is redundant and made the function
    # depend on filesystem state when callers mock _load_json directly.
    try:
        existing = _load_json(cache_path)
        for sym, spec in existing.get('symbols', {}).items():
            merged_symbols[sym] = dict(spec)
    except Exception:
        pass  # no existing cache (or corrupted): start fresh

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
    symbols_hash = _compute_symbols_hash(merged_symbols)
    result['_config_meta'] = {
        'schema_version': '2.0',
        'last_fetched': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'symbols_hash': symbols_hash,
    }

    return result


def _warn_stale_cache(
    cache_path: Path,
    age_days: float,
    broker_type: str,
    error: Exception,
    logger: Optional[ScenarioLogger],
) -> None:
    """
    Log a staleness warning with human-readable instructions.

    Args:
        cache_path: Path to the stale cache file
        age_days: Age of the cache in days
        broker_type: Broker type identifier
        error: Exception from the failed API refresh attempt
        logger: ScenarioLogger for status messages (may be None)
    """
    is_very_stale = age_days > _CACHE_STALE_WARN_DAYS

    try:
        cached_data = _load_json(cache_path)
        config_hash = cached_data.get('_config_meta', {}).get('symbols_hash', '')
        symbols = cached_data.get('symbols', {})
        active_count = sum(1 for s in symbols.values() if s.get('_active', True))
        hash_tag = f' [{config_hash}]' if config_hash else ''
        symbol_summary = f"{active_count} active symbols{hash_tag}"
    except Exception:
        symbol_summary = 'cache unreadable'

    try:
        last_fetched = cached_data.get('_config_meta', {}).get('last_fetched', 'unknown')  # type: ignore[union-attr]
    except Exception:
        last_fetched = 'unknown'

    age_label = f"{age_days:.0f} days ago"

    if is_very_stale:
        msg = (
            f"⚠️  Broker config is {age_days:.0f} days old — symbol specs may be outdated.\n"
            f"    Cache:   {cache_path}\n"
            f"    Cached:  {last_fetched} ({age_label}) — STALE (>{_CACHE_STALE_WARN_DAYS} days)\n"
            f"    Config:  {symbol_summary}\n"
            f"    Reason:  {error}\n"
            f"\n"
            f"    Risk:    Volume limits, tick sizes, or listed symbols may have changed.\n"
            f"             Delisted symbols will continue trading on outdated specs.\n"
            f"    Fix:     Ensure internet / API access and restart to force a refresh.\n"
            f"\n"
            f"    Static seed (for manual inspection and git-tracked reference):\n"
            f"    configs/brokers/kraken/kraken_spot_broker_config.json\n"
            f"    (sync and commit after a successful cache refresh)"
        )
    else:
        msg = (
            f"⚠️  Could not refresh broker config — using cached version.\n"
            f"    Cache:   {cache_path}\n"
            f"    Cached:  {last_fetched} ({age_label}) — within acceptable range\n"
            f"    Config:  {symbol_summary}\n"
            f"    Reason:  {error}\n"
            f"    Next:    A fresh fetch will be attempted on your next session start.\n"
            f"             To force a refresh: ensure API access and restart."
        )

    if logger:
        logger.warning(msg)
    else:
        print(msg)
