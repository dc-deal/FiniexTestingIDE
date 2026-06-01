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

from python.framework.types.config_types.market_config_types import BrokerTransportConfig
from python.framework.types.trading_env_types.broker_trade_types import BrokerTrade
from python.framework.types.trading_env_types.broker_types import BrokerSpecification, BrokerType, MarginMode, SwapMode, SymbolSpecification
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.live_types.live_execution_types import BrokerOrderStatus, BrokerResponse
from python.framework.types.live_types.reconciliation_types import BrokerOrder, BrokerPosition
from .abstract_adapter import AbstractAdapter
from .dry_run_simulator import DryRunOrderSimulator
from python.framework.types.trading_env_types.order_types import (
    OrderCapabilities,
    OrderType,
    MarketOrder,
    LimitOrder,
    StopLimitOrder,
    IcebergOrder,
    OrderDirection,
    OrderSide,
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

    # Kraken descr.ordertype → OrderType (broker truth-pull, #151)
    _ORDERTYPE_MAP: Dict[str, OrderType] = {
        'market': OrderType.MARKET,
        'limit': OrderType.LIMIT,
        'stop-loss': OrderType.STOP,
        'stop-loss-limit': OrderType.STOP_LIMIT,
        'take-profit': OrderType.STOP,
        'take-profit-limit': OrderType.STOP_LIMIT,
    }

    # Sentinel key marking a raw response as a dry-run handoff. The
    # _do_request_* layer tags the raw dict; _parse_*_response detects
    # the tag and delegates to DryRunOrderSimulator for the synthetic
    # BrokerResponse so the response timestamp matches the parse stage.
    _DRY_RUN_SENTINEL = '__dry_run_op__'

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
        self._last_request_time: float = 0.0

        # Dry-run lifecycle simulator. Always instantiated so DRYRUN-*
        # refs remain queryable even if dry_run is toggled off mid-run.
        # Tier-3 transport layers route to it when self._dry_run is True
        # or when the broker_ref carries the DRYRUN-* prefix.
        self._dry_run_simulator: DryRunOrderSimulator = DryRunOrderSimulator()

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

    def enable_live(
        self,
        credentials_file: str,
        dry_run: bool,
        transport: BrokerTransportConfig,
    ) -> None:
        """
        Enable Tier 3 live execution by loading credentials and broker settings.

        Args:
            credentials_file: Credentials filename (resolved via cascade)
            dry_run: True = validate only, no real orders placed
            transport: Per-broker transport tuning (api_base_url, rate_limit_interval_s,
                       request_timeout_s, poll_interval_ms). poll_interval_ms is read
                       by LiveTradeExecutor, not the adapter itself.
        """
        self._api_key, self._api_secret = self._load_credentials(credentials_file)
        self._api_base_url = transport.api_base_url
        self._dry_run = dry_run
        self._rate_limit_interval_s = transport.rate_limit_interval_s
        self._request_timeout_s = transport.request_timeout_s
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
    #
    # Tier 3 is split into three pure layers (transport-neutral contract
    # defined on AbstractAdapter):
    #   _build_*_payload     — pure, no I/O, no state
    #   _do_request_*        — transport (HTTP for Kraken), raises on error
    #   _parse_*_response    — pure w.r.t. broker payload (delegates to
    #                          DryRunOrderSimulator on the dry-run branch)
    #
    # LiveRequestProcessor composes these layers directly — see its
    # submit/query/cancel/modify orchestrators. Dry-run flows through the
    # DryRunOrderSimulator owned by this adapter: _do_request_* runs the
    # real broker call when validation is still desired (validate=true
    # for submit) and tags the raw with the operation kind; the parse
    # layer hands off to the simulator with the parse-stage timestamp.
    # ============================================

    # --- Build payloads (pure) ---

    def _build_submit_payload(
        self,
        symbol: str,
        direction: OrderDirection,
        lots: float,
        order_type: OrderType,
        **kwargs
    ) -> Dict[str, str]:
        """
        Build Kraken AddOrder payload from order parameters.

        Pure — no I/O, no state mutation. The validate flag for dry-run
        is appended by the orchestrator, not here.

        Args:
            symbol: Trading symbol (e.g., 'BTCUSD')
            direction: LONG or SHORT
            lots: Order size
            order_type: MARKET or LIMIT
            **kwargs: price (required for LIMIT)

        Returns:
            Kraken-formatted POST data dict
        """
        pair = self._resolve_kraken_pair(symbol)
        kraken_type = 'buy' if direction == OrderDirection.LONG else 'sell'
        kraken_ordertype = 'market' if order_type == OrderType.MARKET else 'limit'

        data: Dict[str, str] = {
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

        return data

    def _build_query_payload(self, broker_ref: str) -> Dict[str, str]:
        """
        Build Kraken QueryOrders payload.

        Pure — no I/O, no state.

        Args:
            broker_ref: Kraken txid

        Returns:
            POST data dict
        """
        return {'txid': broker_ref}

    def _build_cancel_payload(self, broker_ref: str) -> Dict[str, str]:
        """
        Build Kraken CancelOrder payload.

        Pure — no I/O, no state.

        Args:
            broker_ref: Kraken txid

        Returns:
            POST data dict
        """
        return {'txid': broker_ref}

    def _build_modify_payload(
        self,
        broker_ref: str,
        symbol: str,
        new_price: Optional[float] = None,
        new_stop_loss: Optional[float] = None,
        new_take_profit: Optional[float] = None,
    ) -> Dict[str, str]:
        """
        Build Kraken EditOrder payload.

        Pure — no I/O, no state. Kraken EditOrder requires the pair name
        alongside the txid. Kraken EditOrder does not support SL/TP
        modification — those kwargs are accepted for interface symmetry
        and silently ignored here.

        Args:
            broker_ref: Current Kraken txid
            symbol: Trading symbol (resolved to Kraken pair internally)
            new_price: New limit price (None=no change)
            new_stop_loss: Ignored — Kraken EditOrder does not modify SL
            new_take_profit: Ignored — Kraken EditOrder does not modify TP

        Returns:
            POST data dict
        """
        pair = self._resolve_kraken_pair(symbol)
        data: Dict[str, str] = {'txid': broker_ref, 'pair': pair}
        if new_price is not None:
            data['price'] = str(new_price)
        return data

    def _build_trades_query_payload(self, broker_ref: str) -> Dict[str, str]:
        """
        Build Kraken trades-query payload (#326).

        Pure — no I/O, no state. The payload carries only the order's txid;
        _do_request_trades_query performs the two-call Kraken pattern
        (QueryOrders trades=true → QueryTrades) internally.

        Args:
            broker_ref: Kraken order txid

        Returns:
            POST data dict
        """
        return {'txid': broker_ref}

    # --- HTTP transport (raises on error) ---

    def _do_request_submit(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Send AddOrder request to Kraken. Raises on HTTP/API error.

        In dry-run mode, first calls Kraken with validate=true so the
        broker validates pair, lot size, cost minimum, and margin —
        an invalid order still raises and surfaces as REJECTED. On
        successful validation the call hands off to DryRunOrderSimulator
        via a sentinel-tagged dict so _parse_submit_response can produce
        the synthetic BrokerResponse with the parse-stage timestamp.

        Args:
            payload: Pre-built AddOrder payload

        Returns:
            Raw Kraken result dict (sentinel-tagged in dry-run)
        """
        if self._dry_run:
            self._fetch_private(
                '/0/private/AddOrder',
                {**payload, 'validate': 'true'},
            )
            return {
                self._DRY_RUN_SENTINEL: 'submit',
                'lots': float(payload['volume']),
                'price': float(payload['price']) if 'price' in payload else None,
            }
        return self._fetch_private('/0/private/AddOrder', payload)

    def _do_request_query(self, payload: Dict[str, str]) -> Dict[str, Any]:
        """
        Send QueryOrders request to Kraken. Raises on HTTP/API error.

        Dry-run orders (DRYRUN-* refs) do not exist at the broker —
        delegate to the simulator via a sentinel-tagged dict.

        Args:
            payload: Pre-built QueryOrders payload

        Returns:
            Raw Kraken result dict (sentinel-tagged in dry-run)
        """
        broker_ref = payload['txid']
        if self._dry_run or broker_ref.startswith('DRYRUN-'):
            return {
                self._DRY_RUN_SENTINEL: 'query',
                'broker_ref': broker_ref,
            }
        return self._fetch_private('/0/private/QueryOrders', payload)

    def _do_request_cancel(self, payload: Dict[str, str]) -> Dict[str, Any]:
        """
        Send CancelOrder request to Kraken. Raises on HTTP/API error.

        Dry-run orders (DRYRUN-* refs) do not exist at the broker —
        delegate to the simulator via a sentinel-tagged dict.

        Args:
            payload: Pre-built CancelOrder payload

        Returns:
            Raw Kraken result dict (sentinel-tagged in dry-run)
        """
        broker_ref = payload['txid']
        if self._dry_run or broker_ref.startswith('DRYRUN-'):
            return {
                self._DRY_RUN_SENTINEL: 'cancel',
                'broker_ref': broker_ref,
            }
        return self._fetch_private('/0/private/CancelOrder', payload)

    def _do_request_modify(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Send EditOrder request to Kraken. Raises on HTTP/API error.

        Dry-run orders (DRYRUN-* refs) do not exist at the broker —
        delegate to the simulator via a sentinel-tagged dict that
        carries new_price for ref replacement.

        Args:
            payload: Pre-built EditOrder payload

        Returns:
            Raw Kraken result dict (sentinel-tagged in dry-run)
        """
        broker_ref = payload['txid']
        if self._dry_run or broker_ref.startswith('DRYRUN-'):
            return {
                self._DRY_RUN_SENTINEL: 'modify',
                'broker_ref': broker_ref,
                'new_price': float(payload['price']) if 'price' in payload else None,
            }
        return self._fetch_private('/0/private/EditOrder', payload)

    def _do_request_trades_query(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Fetch per-execution trade records for an order (#326). Two-call Kraken
        pattern internally:
            1. QueryOrders(trades=true)  → list of trade IDs for this order
            2. QueryTrades(txid=ids)     → full trade detail per ID

        Dry-run orders (DRYRUN-* refs) do not produce real trade records —
        returns a sentinel-tagged dict that _parse_trades_query_response
        converts to an empty list.

        Args:
            payload: Pre-built trades-query payload (carries the order's txid)

        Returns:
            Raw Kraken result dict (sentinel-tagged in dry-run; otherwise
            wraps the QueryTrades response under 'trades_raw')
        """
        broker_ref = payload['txid']
        if self._dry_run or broker_ref.startswith('DRYRUN-'):
            return {
                self._DRY_RUN_SENTINEL: 'trades_query',
                'broker_ref': broker_ref,
            }

        # Step 1 — get trade IDs from QueryOrders(trades=true)
        order_resp = self._fetch_private(
            '/0/private/QueryOrders',
            {'txid': broker_ref, 'trades': 'true'},
        )
        order_data = order_resp.get(broker_ref, {})
        trade_ids: List[str] = order_data.get('trades', []) or []
        if not trade_ids:
            return {'broker_ref': broker_ref, 'trades_raw': {}}

        # Step 2 — get full trade detail from QueryTrades (comma-separated ids)
        trades_resp = self._fetch_private(
            '/0/private/QueryTrades',
            {'txid': ','.join(trade_ids)},
        )
        return {'broker_ref': broker_ref, 'trades_raw': trades_resp}

    # --- Parse responses (pure) ---

    def _parse_submit_response(self, raw: Dict[str, Any], timestamp: datetime) -> BrokerResponse:
        """
        Parse Kraken AddOrder response into BrokerResponse.

        Pure w.r.t. broker payload. In dry-run mode delegates to the
        DryRunOrderSimulator (which is stateful — counter + per-order
        tracking) so the response is a freshly-issued PENDING with a
        synthetic DRYRUN-* ref. Real-mode parse is unchanged.

        Args:
            raw: Raw Kraken result dict
            timestamp: Response receipt timestamp (UTC)

        Returns:
            BrokerResponse — PENDING in both modes; dry-run uses
            simulator-issued synthetic ref
        """
        if raw.get(self._DRY_RUN_SENTINEL) == 'submit':
            return self._dry_run_simulator.submit(
                lots=raw['lots'],
                price=raw['price'],
                timestamp=timestamp,
            )

        txid_list = raw.get('txid', [])
        broker_ref = txid_list[0] if txid_list else ''
        return BrokerResponse(
            broker_ref=broker_ref,
            status=BrokerOrderStatus.PENDING,
            timestamp=timestamp,
            raw_response=raw,
        )

    def _parse_query_response(
        self,
        raw: Dict[str, Any],
        broker_ref: str,
        timestamp: datetime,
    ) -> BrokerResponse:
        """
        Parse Kraken QueryOrders response into BrokerResponse.

        Pure w.r.t. broker payload. In dry-run mode delegates to the
        simulator, which advances the per-order poll counter and may
        flip the order from PENDING to FILLED. Real-mode parse maps
        Kraken status codes to BrokerOrderStatus and extracts fill
        data when terminal.

        Args:
            raw: Raw Kraken result dict
            broker_ref: The txid that was queried
            timestamp: Response receipt timestamp (UTC)

        Returns:
            BrokerResponse with current status
        """
        if raw.get(self._DRY_RUN_SENTINEL) == 'query':
            return self._dry_run_simulator.query(broker_ref, timestamp)

        order_info = raw.get(broker_ref, {})
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
            timestamp=timestamp,
            raw_response=raw,
        )

    def _parse_cancel_response(
        self,
        raw: Dict[str, Any],
        broker_ref: str,
        timestamp: datetime,
    ) -> BrokerResponse:
        """
        Parse Kraken CancelOrder response into BrokerResponse.

        Pure w.r.t. broker payload. Dry-run path delegates to the
        simulator so the cancelled ref is removed from its internal
        state (so subsequent queries do not see PENDING).

        Args:
            raw: Raw Kraken result dict
            broker_ref: The txid that was cancelled
            timestamp: Response receipt timestamp (UTC)

        Returns:
            BrokerResponse(status=CANCELLED)
        """
        if raw.get(self._DRY_RUN_SENTINEL) == 'cancel':
            return self._dry_run_simulator.cancel(broker_ref, timestamp)

        return BrokerResponse(
            broker_ref=broker_ref,
            status=BrokerOrderStatus.CANCELLED,
            timestamp=timestamp,
            raw_response=raw,
        )

    def _parse_modify_response(
        self,
        raw: Dict[str, Any],
        original_broker_ref: str,
        timestamp: datetime,
    ) -> BrokerResponse:
        """
        Parse Kraken EditOrder response into BrokerResponse.

        Pure w.r.t. broker payload. EditOrder replaces the order — Kraken
        returns a NEW txid that must replace the original in any tracking
        index; the caller is responsible for that swap. Dry-run path
        delegates to the simulator which carries internal state across
        the ref replacement.

        Args:
            raw: Raw Kraken result dict
            original_broker_ref: The old txid (used as fallback if new txid missing)
            timestamp: Response receipt timestamp (UTC)

        Returns:
            BrokerResponse with NEW broker_ref
        """
        if raw.get(self._DRY_RUN_SENTINEL) == 'modify':
            return self._dry_run_simulator.modify(
                broker_ref=raw['broker_ref'],
                new_price=raw['new_price'],
                timestamp=timestamp,
            )

        new_txid = raw.get('txid', original_broker_ref)
        return BrokerResponse(
            broker_ref=new_txid,
            status=BrokerOrderStatus.PENDING,
            timestamp=timestamp,
            raw_response=raw,
        )

    def _parse_trades_query_response(
        self,
        raw: Dict[str, Any],
        broker_ref: str,
        order_id: str,
    ) -> List[BrokerTrade]:
        """
        Parse Kraken QueryTrades response into List[BrokerTrade] (#326).

        Pure w.r.t. broker payload. Maps each tradeid → BrokerTrade. Kraken
        fields: ordertxid (parent), pair, time (Unix seconds), type
        (buy/sell), ordertype (limit-class = maker), price, vol, fee.

        Dry-run sentinel returns an empty list — synthetic dry-run orders
        do not produce real per-execution detail. Documented limitation.

        Args:
            raw: Raw Kraken result dict (output of _do_request_trades_query)
            broker_ref: The order's broker_ref (cross-checked against ordertxid)
            order_id: OUR internal order_id (injected into every BrokerTrade)

        Returns:
            List of BrokerTrade records, empty if order produced no trades
            (or dry-run path)
        """
        if raw.get(self._DRY_RUN_SENTINEL) == 'trades_query':
            return []

        trades_raw: Dict[str, Any] = raw.get('trades_raw', {}) or {}
        out: List[BrokerTrade] = []
        for trade_id, trade_data in trades_raw.items():
            ordertype = str(trade_data.get('ordertype', ''))
            is_maker = (
                ordertype.startswith('limit')
                or ordertype in ('take-profit-limit', 'stop-loss-limit')
            )
            # Kraken returns 'buy'/'sell' natively — direct mapping to OrderSide.
            side = (
                OrderSide.BUY
                if trade_data.get('type') == 'buy'
                else OrderSide.SELL
            )
            fee_currency = self._resolve_quote_currency_from_pair(
                trade_data.get('pair', '')
            )
            out.append(BrokerTrade(
                trade_id=trade_id,
                parent_broker_ref=trade_data.get('ordertxid', broker_ref),
                order_id=order_id,
                volume=float(trade_data.get('vol', 0.0)),
                price=float(trade_data.get('price', 0.0)),
                fee=float(trade_data.get('fee', 0.0)),
                fee_currency=fee_currency,
                timestamp=datetime.fromtimestamp(
                    float(trade_data.get('time', 0.0)),
                    tz=timezone.utc,
                ),
                side=side,
                is_maker=is_maker,
            ))
        return out

    # ============================================
    # Tier 3 — Broker Truth-Pull (#151 Reconciliation)
    # ============================================
    #
    # In dry-run, the pulls return empty (a sentinel-tagged dict the parser maps
    # to []/{}): synthetic dry-run state has no symbol/direction detail to
    # reconstruct broker truth, and the Reconciler skips DRYRUN-* refs anyway.
    # OpenPositions is margin-only — empty on Kraken spot.
    # ============================================

    def _build_openorders_payload(self) -> Dict[str, str]:
        """
        Build Kraken OpenOrders payload. Pure.

        Returns:
            POST data dict (no required params)
        """
        return {}

    def _build_balance_payload(self) -> Dict[str, str]:
        """
        Build Kraken Balance payload. Pure.

        Returns:
            POST data dict (no required params)
        """
        return {}

    def _build_openpositions_payload(self) -> Dict[str, str]:
        """
        Build Kraken OpenPositions payload. Pure.

        Returns:
            POST data dict (docalcs=true → broker computes net P&L)
        """
        return {'docalcs': 'true'}

    def _do_request_openorders(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Fetch open (resting) orders from Kraken. Raises on HTTP/API error.

        Dry-run returns a sentinel-tagged dict → empty parse.

        Args:
            payload: Pre-built OpenOrders payload

        Returns:
            Raw Kraken result dict (sentinel-tagged in dry-run)
        """
        if self._dry_run:
            return {self._DRY_RUN_SENTINEL: 'openorders'}
        return self._fetch_private('/0/private/OpenOrders', payload)

    def _do_request_balance(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Fetch account balances from Kraken. Raises on HTTP/API error.

        Dry-run returns a sentinel-tagged dict → empty parse.

        Args:
            payload: Pre-built Balance payload

        Returns:
            Raw Kraken result dict (sentinel-tagged in dry-run)
        """
        if self._dry_run:
            return {self._DRY_RUN_SENTINEL: 'balance'}
        return self._fetch_private('/0/private/Balance', payload)

    def _do_request_openpositions(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Fetch open positions from Kraken (margin only). Raises on HTTP/API error.

        Dry-run returns a sentinel-tagged dict → empty parse.

        Args:
            payload: Pre-built OpenPositions payload

        Returns:
            Raw Kraken result dict (sentinel-tagged in dry-run; empty on spot)
        """
        if self._dry_run:
            return {self._DRY_RUN_SENTINEL: 'openpositions'}
        return self._fetch_private('/0/private/OpenPositions', payload)

    def _parse_openorders_response(self, raw: Dict[str, Any]) -> List[BrokerOrder]:
        """
        Parse Kraken OpenOrders response into List[BrokerOrder]. Pure.

        Kraken shape: {'open': {txid: {status, descr:{pair,type,ordertype,price}, vol, ...}}}.

        Args:
            raw: Raw Kraken result dict (output of _do_request_openorders)

        Returns:
            List of BrokerOrder (empty in dry-run / when none open)
        """
        if raw.get(self._DRY_RUN_SENTINEL) is not None:
            return []

        out: List[BrokerOrder] = []
        for txid, info in (raw.get('open', {}) or {}).items():
            descr = info.get('descr', {}) or {}
            kraken_type = descr.get('type', 'buy')
            kraken_ordertype = descr.get('ordertype', 'limit')
            status = self._STATUS_MAP.get(info.get('status', 'open'), BrokerOrderStatus.PENDING)
            price = float(descr.get('price', 0.0) or 0.0) or None
            out.append(BrokerOrder(
                broker_ref=txid,
                symbol=self._resolve_symbol_from_pair(descr.get('pair', '')),
                direction=OrderDirection.LONG if kraken_type == 'buy' else OrderDirection.SHORT,
                order_type=self._ORDERTYPE_MAP.get(kraken_ordertype, OrderType.LIMIT),
                lots=float(info.get('vol', 0.0)),
                status=status,
                price=price,
                raw=info,
            ))
        return out

    def _parse_balance_response(self, raw: Dict[str, Any]) -> Dict[str, float]:
        """
        Parse Kraken Balance response into an asset → amount dict. Pure.

        Kraken shape: {asset_code: amount_str}. Zero balances are dropped.
        Asset codes stay in Kraken form (e.g. 'ZUSD', 'XETH') — normalization
        to standard codes is the Reconciler's concern.

        Args:
            raw: Raw Kraken result dict (output of _do_request_balance)

        Returns:
            Balance dict (empty in dry-run)
        """
        if raw.get(self._DRY_RUN_SENTINEL) is not None:
            return {}

        out: Dict[str, float] = {}
        for asset, amount_str in (raw or {}).items():
            try:
                amount = float(amount_str)
            except (TypeError, ValueError):
                continue
            if amount != 0.0:
                out[asset] = amount
        return out

    def _parse_openpositions_response(self, raw: Dict[str, Any]) -> List[BrokerPosition]:
        """
        Parse Kraken OpenPositions response into List[BrokerPosition]. Pure.

        Margin only — empty on Kraken spot. Kraken shape:
        {txid: {pair, type, vol, cost, net, ...}}. entry_price = cost / vol.

        Args:
            raw: Raw Kraken result dict (output of _do_request_openpositions)

        Returns:
            List of BrokerPosition (empty in dry-run / on spot)
        """
        if raw.get(self._DRY_RUN_SENTINEL) is not None:
            return []

        out: List[BrokerPosition] = []
        for txid, info in (raw or {}).items():
            kraken_type = info.get('type', 'buy')
            vol = float(info.get('vol', 0.0))
            cost = float(info.get('cost', 0.0))
            out.append(BrokerPosition(
                symbol=self._resolve_symbol_from_pair(info.get('pair', '')),
                direction=OrderDirection.LONG if kraken_type == 'buy' else OrderDirection.SHORT,
                lots=vol,
                entry_price=(cost / vol) if vol else 0.0,
                broker_ref=txid,
                unrealized_pnl=float(info['net']) if 'net' in info else None,
                margin_used=float(info['margin']) if 'margin' in info else None,
                raw=info,
            ))
        return out

    def _resolve_symbol_from_pair(self, pair: str) -> str:
        """
        Resolve a Kraken pair string back to the standard symbol from broker config.

        Reverse of _resolve_kraken_pair. Falls back to the pair string itself
        when no kraken_pair_name match is found.

        Args:
            pair: Kraken pair string (e.g., 'XETHZUSD')

        Returns:
            Standard symbol (e.g., 'ETHUSD') or the pair string on no match
        """
        if not pair:
            return ''
        symbols = self.broker_config.get('symbols', {}) or {}
        for symbol, symbol_info in symbols.items():
            if symbol_info.get('kraken_pair_name') == pair:
                return symbol
        return pair

    # ============================================
    # Kraken REST API — HTTP + Signing
    # ============================================

    def _fetch_private(self, endpoint: str, data: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Signed POST to Kraken private API — every private call funnels through here.

        Delegates to _do_fetch_private via the broker-agnostic _timed_call wrapper
        so the API monitor (#351) records per-endpoint latency/errors. The timing
        includes the rate-limit throttle (the real tick-loop-blocking cost).

        Args:
            endpoint: API path (e.g., '/0/private/AddOrder')
            data: Optional POST data

        Returns:
            API result dict
        """
        return self._timed_call(endpoint, lambda: self._do_fetch_private(endpoint, data))

    def _do_fetch_private(self, endpoint: str, data: Optional[Dict] = None) -> Dict[str, Any]:
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

        Uses kraken_pair_name from broker config (static or live-fetched).

        Args:
            symbol: Standard symbol (e.g., 'BTCUSD')

        Returns:
            Kraken pair name (e.g., 'XBTUSD')
        """
        symbol_info = self.broker_config.get('symbols', {}).get(symbol, {})
        pair_name = symbol_info.get('kraken_pair_name', '')
        if pair_name:
            return pair_name

        # Last resort: return symbol as-is
        return symbol

    def _resolve_quote_currency_from_pair(self, pair: str) -> str:
        """
        Resolve a Kraken pair string to the quote currency from broker config.

        Used by _parse_trades_query_response to fill BrokerTrade.fee_currency.
        Iterates broker_config symbols looking for a kraken_pair_name match.
        Falls back to 'USD' if no match (most spot pairs are USD-quoted).

        Args:
            pair: Kraken pair string (e.g., 'XBTUSD', 'XETHZUSD')

        Returns:
            Quote currency code (e.g., 'USD', 'EUR')
        """
        if not pair:
            return 'USD'
        symbols = self.broker_config.get('symbols', {}) or {}
        for symbol_info in symbols.values():
            if symbol_info.get('kraken_pair_name') == pair:
                return symbol_info.get('quote_currency', 'USD')
        return 'USD'

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
