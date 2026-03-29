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
                return self._build_symbol_config(target_symbol, pair_info, _pair_name)

        return None

    def _build_symbol_config(
        self,
        symbol: str,
        pair_info: Dict[str, Any],
        kraken_pair_name: str = '',
    ) -> Dict[str, Any]:
        """
        Build symbol config entry from API pair info.

        Args:
            symbol: Standard symbol (e.g., 'BTCUSD')
            pair_info: Raw API pair data
            kraken_pair_name: Kraken internal pair name (e.g., 'XXBTZUSD') for order API calls

        Returns:
            Symbol config dict matching static JSON structure
        """
        base, quote = self._split_symbol(symbol)

        pair_decimals = pair_info.get('pair_decimals', 1)
        lot_decimals = pair_info.get('lot_decimals', 8)
        ordermin = float(pair_info.get('ordermin', 0.0001))

        tick_size = 10 ** (-pair_decimals)

        return {
            'description': f"{base} vs {quote}",
            'base_currency': base,
            'quote_currency': quote,
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

        Uses hardcoded fee structure (maker 0.16%, taker 0.26%) — Kraken
        fee tiers depend on 30-day rolling volume, static default is safer.

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
                'maker_fee': 0.16,
                'taker_fee': 0.26,
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
