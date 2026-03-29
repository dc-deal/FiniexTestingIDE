"""
FiniexTestingIDE - Kraken Broker Adapter
JSON-based implementation for Kraken crypto exchange

ARCHITECTURE:
- __init__: Reads JSON config (Tier 1+2, backtesting)
- enable_live(): Loads credentials, enables Tier 3 (live execution)
- get_symbol_specification(): From JSON
- get_broker_specification(): From JSON
- Order creation: Tier 1+2 (always available)
- Order execution: Tier 3 (requires enable_live(), Kraken REST API)

Backtesting works without API access. Live trading requires enable_live().
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

from python.framework.types.trading_env_types.broker_types import BrokerSpecification, BrokerType, MarginMode, SwapMode, SymbolSpecification
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.live_types.live_execution_types import BrokerOrderStatus, BrokerResponse
from .abstract_adapter import AbstractAdapter
from python.framework.types.trading_env_types.order_types import (
    OrderCapabilities,
    OrderType,
    MarketOrder,
    LimitOrder,
    StopLimitOrder,
    IcebergOrder,
    OrderDirection,
)


class KrakenAdapter(AbstractAdapter):
    """
    Kraken Crypto Exchange Adapter.

    Tier 1+2: JSON-based config, validation, symbol specs (backtesting, always available).
    Tier 3: Live order execution via Kraken REST API (requires enable_live()).

    Kraken-Specific Features:
    - Maker/Taker fee structure (from fee_structure in config)
    - No swap fees (spot trading)
    - Leverage = 1 for pure spot (no margin)
    - Currencies explicit in config (base_currency, quote_currency)
    - Dry-run mode: validate=true on AddOrder (no execution, no money moved)
    """

    # Kraken order status → BrokerOrderStatus
    _STATUS_MAP: Dict[str, BrokerOrderStatus] = {
        'pending': BrokerOrderStatus.PENDING,
        'open': BrokerOrderStatus.PENDING,
        'closed': BrokerOrderStatus.FILLED,
        'canceled': BrokerOrderStatus.CANCELLED,
        'expired': BrokerOrderStatus.EXPIRED,
    }

    def __init__(self, broker_config: Dict[str, Any]):
        """
        Initialize Kraken adapter with broker configuration.

        Args:
            broker_config: Kraken config loaded from JSON
        """
        super().__init__(broker_config)

        # Cache fee structure
        self._maker_fee = self._get_config_value(
            'fee_structure.maker_fee', 0.16)
        self._taker_fee = self._get_config_value(
            'fee_structure.taker_fee', 0.26)

        # Tier 3 state (disabled until enable_live() is called)
        self._live_enabled: bool = False
        self._api_key: str = ''
        self._api_secret: str = ''
        self._api_base_url: str = ''
        self._dry_run: bool = True
        self._rate_limit_interval_s: float = 1.0
        self._request_timeout_s: int = 15
        self._symbol_to_kraken_pair: Dict[str, str] = {}
        self._last_request_time: float = 0.0
        self._dry_run_counter: int = 0

    # ============================================
    # Configuration
    # ============================================

    def _validate_config(self) -> None:
        """
        Validate Kraken-specific configuration.

        Called after _validate_common_config() in base class.
        """
        # Kraken requires fee_structure with maker_taker model
        fee_structure = self.broker_config.get('fee_structure')
        if not fee_structure:
            raise ValueError(
                "❌ Kraken config requires 'fee_structure' section"
            )

        fee_model = fee_structure.get('model')
        if fee_model != 'maker_taker':
            raise ValueError(
                f"❌ Kraken requires fee_structure.model='maker_taker', got '{fee_model}'"
            )

        # Validate maker/taker fees exist
        if 'maker_fee' not in fee_structure or 'taker_fee' not in fee_structure:
            raise ValueError(
                "❌ Kraken fee_structure requires 'maker_fee' and 'taker_fee'"
            )

    def get_broker_name(self) -> str:
        """Get broker company name."""
        return self._broker_name

    def get_broker_type(self) -> BrokerType:
        """Get broker type identifier."""
        return BrokerType.KRAKEN_SPOT

    # ============================================
    # Capability Queries
    # ============================================

    def get_order_capabilities(self) -> OrderCapabilities:
        """
        Get Kraken order capabilities.

        Kraken supports:
        - Common: Market, Limit
        - Extended: StopLimit, Iceberg
        - NOT supported: Pure Stop orders (Kraken uses StopLimit)
        """
        return OrderCapabilities(
            market_orders=True,
            limit_orders=True,
            stop_orders=False,  # Kraken uses StopLimit instead
            stop_limit_orders=True,
            trailing_stop=False,
            iceberg_orders=True,
            hedging_allowed=self._hedging_allowed,
            partial_fills_supported=True
        )

    # ============================================
    # Order Creation (FEATURE GATED)
    # ============================================

    def create_market_order(
        self,
        symbol: str,
        direction: OrderDirection,
        lots: float,
        **kwargs
    ) -> MarketOrder:
        """
        Create Kraken market order.
        """

        # Validate order
        is_valid, error = self.validate_order(symbol, lots)
        if not is_valid:
            raise ValueError(f"Invalid market order: {error}")

        return MarketOrder(
            symbol=symbol,
            direction=direction,
            lots=lots,
            stop_loss=kwargs.get('stop_loss'),
            take_profit=kwargs.get('take_profit'),
            max_slippage=kwargs.get('max_slippage'),
            comment=kwargs.get('comment', ''),
        )

    def create_limit_order(
        self,
        symbol: str,
        direction: OrderDirection,
        lots: float,
        price: float,
        **kwargs
    ) -> LimitOrder:
        """
        Create Kraken limit order.
        """

        # Validate order
        is_valid, error = self.validate_order(symbol, lots)
        if not is_valid:
            raise ValueError(f"Invalid limit order: {error}")

        if price <= 0:
            raise ValueError(f"Invalid limit price: {price}")

        return LimitOrder(
            symbol=symbol,
            direction=direction,
            lots=lots,
            price=price,
            stop_loss=kwargs.get('stop_loss'),
            take_profit=kwargs.get('take_profit'),
            expiration=kwargs.get('expiration'),
            comment=kwargs.get('comment', ''),
        )

    def create_stop_limit_order(
        self,
        symbol: str,
        direction: OrderDirection,
        lots: float,
        stop_price: float,
        limit_price: float,
        **kwargs
    ) -> StopLimitOrder:
        """
        Create Kraken stop-limit order.
        """

        # Validate order
        is_valid, error = self.validate_order(symbol, lots)
        if not is_valid:
            raise ValueError(f"Invalid stop-limit order: {error}")

        if stop_price <= 0:
            raise ValueError(f"Invalid stop price: {stop_price}")
        if limit_price <= 0:
            raise ValueError(f"Invalid limit price: {limit_price}")

        return StopLimitOrder(
            symbol=symbol,
            direction=direction,
            lots=lots,
            stop_price=stop_price,
            limit_price=limit_price,
            stop_loss=kwargs.get('stop_loss'),
            take_profit=kwargs.get('take_profit'),
            comment=kwargs.get('comment', ''),
        )

    def create_iceberg_order(
        self,
        symbol: str,
        direction: OrderDirection,
        lots: float,
        visible_lots: float,
        price: float,
        **kwargs
    ) -> IcebergOrder:
        """
        Create Kraken iceberg order.
        """

        # Validate order
        is_valid, error = self.validate_order(symbol, lots)
        if not is_valid:
            raise ValueError(f"Invalid iceberg order: {error}")

        if visible_lots > lots:
            raise ValueError(
                f"Visible lots ({visible_lots}) cannot exceed total lots ({lots})"
            )

        return IcebergOrder(
            symbol=symbol,
            direction=direction,
            lots=lots,
            visible_lots=visible_lots,
            price=price,
            stop_loss=kwargs.get('stop_loss'),
            take_profit=kwargs.get('take_profit'),
            comment=kwargs.get('comment', ''),
        )

    # ============================================
    # Order Validation (JSON-based)
    # ============================================

    def validate_order(
        self,
        symbol: str,
        lots: float
    ) -> tuple[bool, Optional[str]]:
        """
        Validate order parameters against Kraken limits.

        Args:
            symbol: Trading symbol
            lots: Order size

        Returns:
            (is_valid, error_message)
        """
        # Check symbol exists
        if symbol not in self.broker_config['symbols']:
            available = list(self.broker_config['symbols'].keys())
            raise ValueError(
                f"Symbol '{symbol}' not found. Available: {available}"
            )

        symbol_info = self.broker_config['symbols'][symbol]

        # Check trading allowed
        if not symbol_info.get('trade_allowed', True):
            return False, f"Trading not allowed for {symbol}"

        # Use common lot size validation from base class
        return self._validate_lot_size(symbol, lots)

    # ============================================
    # Symbol Information (JSON-based)
    # ============================================

    def get_all_aviable_symbols(self) -> List[str]:
        """
        Return list of all configured symbols.

        Returns:
            List of symbol strings (e.g., ["BTCUSD", "ETHUSD"])
        """
        return list(self.broker_config['symbols'].keys())

    def get_symbol_specification(self, symbol: str) -> SymbolSpecification:
        """
        Get fully typed symbol specification from JSON config.

        Args:
            symbol: Trading symbol (e.g., "BTCUSD")

        Returns:
            SymbolSpecification with all static properties
        """
        if symbol not in self.broker_config['symbols']:
            available = list(self.broker_config['symbols'].keys())
            raise ValueError(
                f"Symbol '{symbol}' not found in Kraken config.\n"
                f"Available symbols: {available}"
            )

        raw = self.broker_config['symbols'][symbol]

        # Kraken has explicit currencies in config
        base_currency = raw.get('base_currency', symbol[:3])
        quote_currency = raw.get('quote_currency', symbol[3:])
        # Crypto spot: margin in quote currency (USD usually)
        margin_currency = quote_currency

        return SymbolSpecification(
            # Identity
            symbol=symbol,
            description=raw.get('description', ''),

            # Trading Limits
            volume_min=raw.get('volume_min', 0.0001),
            volume_max=raw.get('volume_max', 10000.0),
            volume_step=raw.get('volume_step', 0.00000001),
            volume_limit=raw.get('volume_limit', 0.0),

            # Price Properties
            tick_size=raw.get('tick_size', 0.01),
            digits=raw.get('digits', 2),
            contract_size=raw.get('contract_size', 1),

            # Currency Information (explicit in Kraken config)
            base_currency=base_currency,
            quote_currency=quote_currency,
            margin_currency=margin_currency,

            # Trading Permissions
            trade_allowed=raw.get('trade_allowed', True),

            # Swap Configuration (none for crypto spot)
            swap_mode=SwapMode.NONE,
            swap_long=0.0,
            swap_short=0.0,
            swap_rollover3days=0,

            # Order Restrictions
            stops_level=raw.get('stops_level', 0),
            freeze_level=raw.get('freeze_level', 0)
        )

    def get_broker_specification(self) -> BrokerSpecification:
        """
        Get fully typed broker specification from JSON config.

        Returns:
            BrokerSpecification with all static broker properties
        """
        broker_info = self.broker_config.get('broker_info', {})
        trading_permissions = self.broker_config.get('trading_permissions', {})

        # Determine margin mode based on leverage
        leverage = broker_info.get('leverage', 1)
        if leverage == 1:
            margin_mode = MarginMode.NONE
        else:
            margin_mode_str = broker_info.get('margin_mode', 'retail_netting')
            try:
                margin_mode = MarginMode(margin_mode_str.lower())
            except ValueError:
                margin_mode = MarginMode.RETAIL_NETTING

        return BrokerSpecification(
            # Broker Identity
            company=broker_info.get('company', 'Kraken'),
            server=broker_info.get('server', 'kraken_spot'),
            broker_type=self.get_broker_type(),

            # Account Type
            trade_mode=broker_info.get('trade_mode', 'demo'),

            # Leverage & Margin (spot = no margin)
            leverage=leverage,
            margin_mode=margin_mode,
            margin_call_level=broker_info.get('margin_call_level', 0.0),
            stopout_level=broker_info.get('stopout_level', 0.0),
            stopout_mode=broker_info.get('stopout_mode', 'percent'),

            # Trading Permissions
            trade_allowed=trading_permissions.get('trade_allowed', True),
            expert_allowed=True,  # Always true for API trading
            hedging_allowed=broker_info.get('hedging_allowed', False),
            limit_orders=trading_permissions.get('limit_orders', 1000)
        )

# ============================================
# Fee Getters (Kraken-specific)
# ============================================

    def get_maker_fee(self) -> float:
        """
        Get maker fee percentage.

        Maker = adds liquidity (limit orders that don't immediately fill)

        Returns:
            Maker fee as percentage (e.g., 0.16 for 0.16%)
        """
        return self._maker_fee

    def get_taker_fee(self) -> float:
        """
        Get taker fee percentage.

        Taker = removes liquidity (market orders, limit orders that fill immediately)

        Returns:
            Taker fee as percentage (e.g., 0.26 for 0.26%)
        """
        return self._taker_fee

    # ============================================
    # Live Execution — Tier 3 Setup
    # ============================================

    def enable_live(self, broker_settings: Dict[str, Any]) -> None:
        """
        Enable Tier 3 live execution by loading credentials and broker settings.

        Args:
            broker_settings: Parsed broker settings dict with credentials_file, api_base_url, dry_run, rate_limit_interval_s, request_timeout_s, symbol_to_kraken_pair
        """
        credentials_file = broker_settings.get('credentials_file', 'kraken_credentials.json')
        self._api_key, self._api_secret = self._load_credentials(credentials_file)
        self._api_base_url = broker_settings.get('api_base_url', 'https://api.kraken.com')
        self._dry_run = broker_settings.get('dry_run', True)
        self._rate_limit_interval_s = broker_settings.get('rate_limit_interval_s', 1.0)
        self._request_timeout_s = broker_settings.get('request_timeout_s', 15)
        self._symbol_to_kraken_pair = broker_settings.get('symbol_to_kraken_pair', {})
        self._live_enabled = True

    def is_live_capable(self) -> bool:
        """
        Whether this adapter supports live order execution.

        Returns:
            True after enable_live() has been called with valid credentials
        """
        return self._live_enabled

    def get_dry_run(self) -> bool:
        """
        Whether dry-run mode is active (validate only, no execution).

        Returns:
            True if dry_run is enabled
        """
        return self._dry_run

    # ============================================
    # Live Execution — Tier 3 Methods
    # ============================================

    def execute_order(
        self,
        symbol: str,
        direction: OrderDirection,
        lots: float,
        order_type: OrderType,
        **kwargs
    ) -> BrokerResponse:
        """
        Send order to Kraken via POST /0/private/AddOrder.

        In dry-run mode, appends validate=true — Kraken validates but does not execute.

        Args:
            symbol: Trading symbol (e.g., 'BTCUSD')
            direction: LONG or SHORT
            lots: Order size
            order_type: MARKET or LIMIT
            **kwargs: price (for limit), stop_loss, take_profit

        Returns:
            BrokerResponse with broker_ref (txid) and status
        """
        pair = self._resolve_kraken_pair(symbol)
        kraken_type = 'buy' if direction == OrderDirection.LONG else 'sell'
        kraken_ordertype = 'market' if order_type == OrderType.MARKET else 'limit'

        data = {
            'pair': pair,
            'type': kraken_type,
            'ordertype': kraken_ordertype,
            'volume': str(lots),
        }

        # Limit orders require a price
        if order_type == OrderType.LIMIT:
            price = kwargs.get('price')
            if price is not None:
                data['price'] = str(price)

        # Dry-run: validate only
        if self._dry_run:
            data['validate'] = 'true'

        now = datetime.now(timezone.utc)

        try:
            result = self._fetch_private('/0/private/AddOrder', data)
        except Exception as e:
            return BrokerResponse(
                broker_ref='',
                status=BrokerOrderStatus.REJECTED,
                rejection_reason=str(e),
                timestamp=now,
            )

        # Dry-run: synthetic response (Kraken returns no txid in validate mode)
        if self._dry_run:
            self._dry_run_counter += 1
            return BrokerResponse(
                broker_ref=f"DRYRUN-{self._dry_run_counter:06d}",
                status=BrokerOrderStatus.FILLED,
                fill_price=kwargs.get('price') or kwargs.get('expected_price'),
                filled_lots=lots,
                timestamp=now,
                raw_response=result,
            )

        # Real execution: extract txid
        txid_list = result.get('txid', [])
        broker_ref = txid_list[0] if txid_list else ''

        # Market orders on Kraken typically fill immediately (status=closed)
        descr = result.get('descr', {})

        return BrokerResponse(
            broker_ref=broker_ref,
            status=BrokerOrderStatus.PENDING,
            timestamp=now,
            raw_response=result,
        )

    def check_order_status(self, broker_ref: str) -> BrokerResponse:
        """
        Poll Kraken for order status via POST /0/private/QueryOrders.

        Args:
            broker_ref: Kraken txid

        Returns:
            BrokerResponse with current status
        """
        now = datetime.now(timezone.utc)

        # Dry-run orders don't exist at broker
        if self._dry_run or broker_ref.startswith('DRYRUN-'):
            return BrokerResponse(
                broker_ref=broker_ref,
                status=BrokerOrderStatus.FILLED,
                timestamp=now,
            )

        try:
            result = self._fetch_private('/0/private/QueryOrders', {'txid': broker_ref})
        except Exception as e:
            return BrokerResponse(
                broker_ref=broker_ref,
                status=BrokerOrderStatus.REJECTED,
                rejection_reason=str(e),
                timestamp=now,
            )

        order_info = result.get(broker_ref, {})
        kraken_status = order_info.get('status', 'pending')
        status = self._STATUS_MAP.get(kraken_status, BrokerOrderStatus.PENDING)

        fill_price = None
        filled_lots = None
        if status == BrokerOrderStatus.FILLED:
            fill_price = float(order_info.get('price', 0))
            filled_lots = float(order_info.get('vol_exec', 0))

        return BrokerResponse(
            broker_ref=broker_ref,
            status=status,
            fill_price=fill_price,
            filled_lots=filled_lots,
            timestamp=now,
            raw_response=result,
        )

    def cancel_order(self, broker_ref: str) -> BrokerResponse:
        """
        Cancel order at Kraken via POST /0/private/CancelOrder.

        Args:
            broker_ref: Kraken txid

        Returns:
            BrokerResponse with cancellation status
        """
        now = datetime.now(timezone.utc)

        # Dry-run orders don't exist at broker
        if self._dry_run or broker_ref.startswith('DRYRUN-'):
            return BrokerResponse(
                broker_ref=broker_ref,
                status=BrokerOrderStatus.CANCELLED,
                timestamp=now,
            )

        try:
            result = self._fetch_private('/0/private/CancelOrder', {'txid': broker_ref})
        except Exception as e:
            return BrokerResponse(
                broker_ref=broker_ref,
                status=BrokerOrderStatus.REJECTED,
                rejection_reason=str(e),
                timestamp=now,
            )

        return BrokerResponse(
            broker_ref=broker_ref,
            status=BrokerOrderStatus.CANCELLED,
            timestamp=now,
            raw_response=result,
        )

    def modify_order(
        self,
        broker_ref: str,
        new_price: Optional[float] = None,
        new_stop_loss: Optional[float] = None,
        new_take_profit: Optional[float] = None,
    ) -> BrokerResponse:
        """
        Modify order at Kraken via POST /0/private/EditOrder.

        Kraken EditOrder replaces the order — returns a NEW txid. The old txid becomes invalid.
        The caller must update tracking accordingly.

        Args:
            broker_ref: Current Kraken txid
            new_price: New limit price (None=no change)
            new_stop_loss: New stop loss level (None=no change)
            new_take_profit: New take profit level (None=no change)

        Returns:
            BrokerResponse with NEW broker_ref (new txid from Kraken)
        """
        now = datetime.now(timezone.utc)

        # Dry-run orders don't exist at broker
        if self._dry_run or broker_ref.startswith('DRYRUN-'):
            self._dry_run_counter += 1
            return BrokerResponse(
                broker_ref=f"DRYRUN-{self._dry_run_counter:06d}",
                status=BrokerOrderStatus.PENDING,
                timestamp=now,
            )

        data: Dict[str, str] = {'txid': broker_ref}
        if new_price is not None:
            data['price'] = str(new_price)

        try:
            result = self._fetch_private('/0/private/EditOrder', data)
        except Exception as e:
            return BrokerResponse(
                broker_ref=broker_ref,
                status=BrokerOrderStatus.REJECTED,
                rejection_reason=str(e),
                timestamp=now,
            )

        # EditOrder returns new txid
        new_txid = result.get('txid', broker_ref)

        return BrokerResponse(
            broker_ref=new_txid,
            status=BrokerOrderStatus.PENDING,
            timestamp=now,
            raw_response=result,
        )

    # ============================================
    # Kraken REST API — HTTP + Signing
    # ============================================

    def _fetch_private(self, endpoint: str, data: Optional[Dict] = None) -> Dict[str, Any]:
        """
        POST request to Kraken private API with HMAC-SHA512 signing.

        Args:
            endpoint: API path (e.g., '/0/private/AddOrder')
            data: Optional POST data

        Returns:
            API result dict
        """
        if data is None:
            data = {}

        # Rate limiting
        self._enforce_rate_limit()

        headers = self._sign_request(endpoint, data)
        url = f"{self._api_base_url}{endpoint}"

        response = requests.post(
            url,
            headers=headers,
            data=data,
            timeout=self._request_timeout_s,
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
            url_path: API endpoint path (e.g., '/0/private/AddOrder')
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

    def _enforce_rate_limit(self) -> None:
        """Enforce minimum interval between API calls."""
        now = time.monotonic()
        elapsed = now - self._last_request_time
        if elapsed < self._rate_limit_interval_s:
            time.sleep(self._rate_limit_interval_s - elapsed)
        self._last_request_time = time.monotonic()

    # ============================================
    # Symbol Mapping
    # ============================================

    def _resolve_kraken_pair(self, symbol: str) -> str:
        """
        Resolve standard symbol to Kraken pair name for order API calls.

        Uses kraken_pair_name from live-fetched config, falls back to static mapping.

        Args:
            symbol: Standard symbol (e.g., 'BTCUSD')

        Returns:
            Kraken pair name (e.g., 'XXBTZUSD' or 'XBTUSD')
        """
        # Try live-fetched pair name first
        symbol_info = self.broker_config.get('symbols', {}).get(symbol, {})
        pair_name = symbol_info.get('kraken_pair_name', '')
        if pair_name:
            return pair_name

        # Fallback to broker settings mapping
        if symbol in self._symbol_to_kraken_pair:
            return self._symbol_to_kraken_pair[symbol]

        # Last resort: return symbol as-is
        return symbol

    # ============================================
    # Credentials Loading
    # ============================================

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
