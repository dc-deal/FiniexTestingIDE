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
import threading
import time
import urllib.parse
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests

from python.configuration.credential_guard import assert_real_credential
from python.framework.exceptions.connection_errors import ConnectionAttemptFailedError
from python.framework.types.config_types.market_config_types import BrokerTransportConfig
from python.framework.types.live_types.live_execution_types import BrokerOrderStatus, BrokerResponse
from python.framework.types.live_types.reconciliation_types import BrokerOrder, BrokerPosition
from python.framework.types.trading_env_types.broker_trade_types import BrokerTrade
from python.framework.types.trading_env_types.broker_types import (
    BrokerSpecification,
    BrokerType,
    MarginMode,
    SwapMode,
    SymbolSpecification,
)
from python.framework.types.trading_env_types.order_types import (
    IcebergOrder,
    LimitOrder,
    MarketOrder,
    OrderCapabilities,
    OrderDirection,
    OrderSide,
    OrderType,
    StopLimitOrder,
)
from python.framework.utils.connection_ladder import is_terminal_status

from .abstract_adapter import AbstractAdapter
from .dry_run_simulator import DryRunOrderSimulator

# Kraken's limit for a client order id, verified against their API docs (#355).
_CL_ORD_ID_MAX_LEN: int = 18


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
    # The take-profit pair maps onto STOP because this project has no inverted-trigger type;
    # both are "wait for a price, then act". The trailing pair collapses onto TRAILING_STOP
    # for the same reason — a read at this granularity says "a trailing stop", which is true,
    # and nothing routable accepts the type anyway. `settle-position` is deliberately absent:
    # it is a margin instrument and cannot occur on a spot account.
    _ORDERTYPE_MAP: Dict[str, OrderType] = {
        'market': OrderType.MARKET,
        'limit': OrderType.LIMIT,
        'stop-loss': OrderType.STOP,
        'stop-loss-limit': OrderType.STOP_LIMIT,
        'take-profit': OrderType.STOP,
        'take-profit-limit': OrderType.STOP_LIMIT,
        'trailing-stop': OrderType.TRAILING_STOP,
        'trailing-stop-limit': OrderType.TRAILING_STOP,
        'iceberg': OrderType.ICEBERG,
    }

    # Types whose `descr.price` is a TRIGGER rather than a limit price, and whose
    # `descr.price2` carries the limit where they have one.
    _TRIGGER_PRICED_ORDERTYPES = frozenset({
        'stop-loss', 'stop-loss-limit', 'take-profit', 'take-profit-limit',
    })
    # Types whose price fields are RELATIVE offsets to the last traded price, not prices.
    # Reading either as an absolute number produces a plausible-looking wrong level.
    _OFFSET_PRICED_ORDERTYPES = frozenset({'trailing-stop', 'trailing-stop-limit'})

    # Sentinel key marking a raw response as a dry-run handoff. The
    # _do_request_* layer tags the raw dict; _parse_*_response detects
    # the tag and delegates to DryRunOrderSimulator for the synthetic
    # BrokerResponse so the response timestamp matches the parse stage.
    _DRY_RUN_SENTINEL = '__dry_run_op__'
    # The venue's own answer to the validate call, carried through the same handoff. It is
    # the ONLY thing that says what Kraken UNDERSTOOD of the order — its `descr` names the
    # resolved pair, the effective order type and any conditional close. Without it a
    # dry-run test can observe nothing but "the call did not raise", which is why one named
    # `..._with_sltp_dryrun` passed while the levels reached nothing.
    _DRY_RUN_VALIDATED = '__dry_run_validated__'

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
        # Serialize private API calls + strictly-monotone nonce (see _do_fetch_private)
        self._private_lock = threading.Lock()
        self._last_nonce: int = 0

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
        Get Kraken order capabilities — what the VENUE offers, not what we route.

        Kraken Spot supports:
        - Common: Market, Limit
        - Extended: Stop, StopLimit, Iceberg
        - NOT supported: trailing stops through this adapter (Kraken offers
          `trailing-stop` on spot, but nothing here builds or triggers one)

        `stop_orders` said False until #500, with the reason "Kraken uses StopLimit
        instead" — which is not true: Kraken offers a plain `stop-loss` that triggers to
        market, and this adapter's own READ side has always mapped it to OrderType.STOP.
        The write half denied what the read half accepted. Declaring the venue truthfully
        is separate from declaring what the pipeline has built: the pre-flight intersects
        this with the executor's own set and names whichever side is short.

        `iceberg_orders` stays True for the same reason — AddOrder carries `iceberg` and
        `displayvol` — even though no executor branch places one. That is the pipeline
        being short, and the intersection already says so.
        """
        return OrderCapabilities(
            market_orders=True,
            limit_orders=True,
            stop_orders=True,
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
            raise ValueError(f'Invalid market order: {error}')

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
            raise ValueError(f'Invalid limit order: {error}')

        if price <= 0:
            raise ValueError(f'Invalid limit price: {price}')

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
            raise ValueError(f'Invalid stop-limit order: {error}')

        if stop_price <= 0:
            raise ValueError(f'Invalid stop price: {stop_price}')
        if limit_price <= 0:
            raise ValueError(f'Invalid limit price: {limit_price}')

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
            raise ValueError(f'Invalid iceberg order: {error}')

        if visible_lots > lots:
            raise ValueError(
                f'Visible lots ({visible_lots}) cannot exceed total lots ({lots})'
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
            return False, f'Trading not allowed for {symbol}'

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
            order_type: MARKET, LIMIT, STOP or STOP_LIMIT
            **kwargs: limit_price (LIMIT and STOP_LIMIT), stop_price (STOP/STOP_LIMIT
                trigger), client_order_id (#473)

        Returns:
            Kraken-formatted POST data dict
        """
        pair = self._resolve_kraken_pair(symbol)
        kraken_type = 'buy' if direction == OrderDirection.LONG else 'sell'
        # Only the types this builder knows are mapped. Anything else used to fall through
        # to 'limit' silently — so a STOP_LIMIT, had the executor's gate ever let one through,
        # would have gone on the wire as a plain LIMIT at its limit price. Refusing here is the
        # second line behind the executor's own gate: the wire cannot receive a type the
        # builder has not been taught.
        #
        # STOP maps to Kraken's `stop-loss` rather than `take-profit`: this project's STOP is
        # a breakout entry (buy above the market, sell below), which is exactly how Kraken
        # defines stop-loss. `take-profit` is the inverted side and has no type here.
        if order_type == OrderType.MARKET:
            kraken_ordertype = 'market'
        elif order_type == OrderType.LIMIT:
            kraken_ordertype = 'limit'
        elif order_type == OrderType.STOP:
            kraken_ordertype = 'stop-loss'
        elif order_type == OrderType.STOP_LIMIT:
            kraken_ordertype = 'stop-loss-limit'
        else:
            raise ValueError(
                f'KrakenAdapter cannot build an AddOrder payload for {order_type.value}: '
                f'MARKET, LIMIT, STOP and STOP_LIMIT are mapped to a Kraken ordertype. '
                f'Another type needs its own mapping before the executor may route it.'
            )

        data: Dict[str, str] = {
            'pair': pair,
            'type': kraken_type,
            'ordertype': kraken_ordertype,
            'volume': str(lots),
        }

        # Kraken's two price fields, and their meaning depends on the ordertype:
        #   limit             price = limit
        #   stop-loss         price = TRIGGER
        #   stop-loss-limit   price = TRIGGER, price2 = limit
        # There is no `stopprice` request parameter — that name exists only in Kraken's
        # RESPONSES (OpenOrders / QueryOrders), and sending it would be silently ignored.
        # A resting type with no price is OUR defect, and it must not be posted. Kraken
        # answers `EGeneral:Invalid arguments:price`, which costs a round trip and reads
        # like a venue problem — while the real cause is a caller that named the key
        # something else. The executor's own gate refuses this first; this is the second
        # line behind it, for the same reason the unmapped-type branch raises.
        if order_type == OrderType.LIMIT:
            self._require_price(data, 'price', kwargs.get('limit_price'),
                                order_type, 'limit_price')
        elif order_type == OrderType.STOP:
            self._require_price(data, 'price', kwargs.get('stop_price'),
                                order_type, 'stop_price')
        elif order_type == OrderType.STOP_LIMIT:
            self._require_price(data, 'price', kwargs.get('stop_price'),
                                order_type, 'stop_price')
            self._require_price(data, 'price2', kwargs.get('limit_price'),
                                order_type, 'limit_price')

        # #473 — a key we chose ourselves, so a submit whose answer was lost can still be
        # ASKED about: the txid is exactly what did not arrive. Kraken accepts free-format
        # ASCII up to 18 characters and returns it in OpenOrders / QueryOrders.
        client_order_id = kwargs.get('client_order_id')
        if client_order_id:
            data['cl_ord_id'] = str(client_order_id)[:_CL_ORD_ID_MAX_LEN]

        return data

    @classmethod
    def _require_price(
        cls,
        data: Dict[str, str],
        key: str,
        value: Optional[float],
        order_type: OrderType,
        kwarg_name: str,
    ) -> None:
        """
        Write a price field a resting type cannot go on the wire without. Pure.

        `_put_price` treats None as "omit the field", which is right for an optional price.
        For a resting type the price is not optional, and omitting it silently produced an
        order the venue refused for a reason that named its own parameter rather than our
        missing kwarg. Measured 2026-09-07 against the live API: renaming the LIMIT kwarg
        left a caller passing the old name, and the only symptom was
        `EGeneral:Invalid arguments:price` from Kraken.

        Args:
            data: The payload being built, mutated in place
            key: The Kraken field to write
            value: The price, or None — which is the error
            order_type: The type being built, for the message
            kwarg_name: The kwarg this price was expected under, for the message

        Returns:
            None
        """
        if value is None:
            raise ValueError(
                f'KrakenAdapter cannot build an AddOrder payload for '
                f'{order_type.value}: no {kwarg_name} was passed, so Kraken\'s "{key}" '
                f'field would be missing. The venue would refuse this naming its own '
                f'parameter, which hides that the caller used a different kwarg name.'
            )
        cls._put_price(data, key, value)

    @staticmethod
    def _put_price(data: Dict[str, str], key: str, value: Optional[float]) -> None:
        """
        Write a price field, refusing anything Kraken would read as an offset. Pure.

        Kraken's `price` and `price2` accept a leading `+`, `-` or `#`, and a trailing `%`,
        to mean an amount RELATIVE to the last traded price. So `str(-1.5)` is not an error
        the venue reports back — it is a valid order 1.5 below the last trade, at a price
        nobody chose. A non-positive number can only be a defect on our side, and it has to
        fail here rather than execute there.

        Args:
            data: The payload being built, mutated in place
            key: 'price' or 'price2'
            value: The absolute price to send, or None to omit the field
        """
        if value is None:
            return
        if value <= 0:
            raise ValueError(
                f'KrakenAdapter refuses to send {key}={value!r}: Kraken reads a signed or '
                f'percentage-suffixed price as an offset from the last traded price, so a '
                f'non-positive value would place a valid order at an unintended price.'
            )
        data[key] = str(value)

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
        order_type: OrderType,
        new_price: Optional[float] = None,
        new_limit_price: Optional[float] = None,
        new_stop_loss: Optional[float] = None,
        new_take_profit: Optional[float] = None,
    ) -> Dict[str, str]:
        """
        Build Kraken AmendOrder payload.

        Pure — no I/O, no state. Kraken AmendOrder modifies the order
        in-place (no cancel-replace): it targets the order by txid and
        keeps the same order identifiers. No pair is required. AmendOrder
        does not modify SL/TP — those kwargs are accepted for interface
        symmetry and silently ignored here.

        AmendOrder has TWO price fields and they are not interchangeable:
        `trigger_price` activates a triggered type, `limit_price` is what it fills at.
        Everything used to go into `limit_price`, so amending a resting stop's trigger
        would have moved its limit instead — and a STOP_LIMIT's limit amend never left
        the process at all (#500).

        Args:
            broker_ref: Current Kraken txid (the order to amend)
            symbol: Trading symbol — unused (AmendOrder targets by txid)
            order_type: The type being amended — decides which field new_price goes to
            new_price: New limit price, or the new TRIGGER of a triggered type
                (None=no change)
            new_limit_price: New limit price of a STOP_LIMIT (None=no change)
            new_stop_loss: Ignored — Kraken AmendOrder does not modify SL
            new_take_profit: Ignored — Kraken AmendOrder does not modify TP

        Returns:
            POST data dict
        """
        data: Dict[str, str] = {'txid': broker_ref}
        if order_type in (OrderType.STOP, OrderType.STOP_LIMIT):
            self._put_price(data, 'trigger_price', new_price)
            self._put_price(data, 'limit_price', new_limit_price)
        else:
            self._put_price(data, 'limit_price', new_price)
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
            validated = self._fetch_private(
                '/0/private/AddOrder',
                {**payload, 'validate': 'true'},
            )
            return {
                self._DRY_RUN_SENTINEL: 'submit',
                self._DRY_RUN_VALIDATED: validated,
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
        Send AmendOrder request to Kraken. Raises on HTTP/API error.

        Dry-run orders (DRYRUN-* refs) do not exist at the broker —
        delegate to the simulator via a sentinel-tagged dict that
        carries new_price for the in-place amend.

        Args:
            payload: Pre-built AmendOrder payload

        Returns:
            Raw Kraken result dict (sentinel-tagged in dry-run)
        """
        broker_ref = payload['txid']
        if self._dry_run or broker_ref.startswith('DRYRUN-'):
            return {
                self._DRY_RUN_SENTINEL: 'modify',
                'broker_ref': broker_ref,
                'new_price': float(payload['limit_price']) if 'limit_price' in payload else None,
            }
        return self._fetch_private('/0/private/AmendOrder', payload)

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
            response = self._dry_run_simulator.submit(
                lots=raw['lots'],
                price=raw['price'],
                timestamp=timestamp,
            )
            # The simulator issues the synthetic ref; the VENUE said what it made of the
            # order. Both belong on the response — the ref is ours, the description is
            # Kraken's, and only the second one can be asserted against.
            return replace(response, raw_response=raw.get(self._DRY_RUN_VALIDATED))

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

        # An answer that does not mention the txid is not a state, and it used to become
        # one: `raw.get(broker_ref, {})` then `.get('status', 'pending')` turned "Kraken has
        # never heard of this order" into "it is still working". Measured 2026-09-08 —
        # QueryOrders for a txid Kraken never minted returns `{}`, and the parse reported
        # PENDING with is_terminal False. The same applies to a status WORD we do not map:
        # we cannot name the state, so we must not name one. Both become UNKNOWN.
        order_info = raw.get(broker_ref)
        if order_info is None:
            return BrokerResponse(
                broker_ref=broker_ref,
                status=BrokerOrderStatus.UNKNOWN,
                timestamp=timestamp,
                raw_response=raw,
            )

        kraken_status = order_info.get('status', '')
        status = self._STATUS_MAP.get(kraken_status, BrokerOrderStatus.UNKNOWN)

        # `vol_exec` is read on EVERY status, not only FILLED. Kraken has no
        # PARTIALLY_FILLED: a half-filled order stays `open` and reports what already
        # executed alongside it — so reading the field only on FILLED made a partial fill
        # not merely unhandled but unrepresentable, and the executed half invisible until
        # the rest filled. A CANCELLED or EXPIRED order can carry one too, which is the
        # dangerous form: the venue took part of it and then the order ended.
        # `price` is Kraken's average execution price and is meaningless at zero volume.
        executed = float(order_info.get('vol_exec', 0.0) or 0.0)
        filled_lots = executed if executed > 0.0 else None
        fill_price = (float(order_info.get('price', 0.0) or 0.0)
                      if filled_lots is not None else None)

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
        Parse Kraken AmendOrder response into BrokerResponse.

        Pure w.r.t. broker payload. AmendOrder modifies the order in-place:
        the order identifiers stay the same, so the broker_ref is unchanged
        (the response carries an amend_id in raw_response for auditing). No
        index swap is required. Dry-run path delegates to the simulator,
        which applies the new price in-place under the same ref.

        Args:
            raw: Raw Kraken result dict
            original_broker_ref: The order's txid (unchanged by the amend)
            timestamp: Response receipt timestamp (UTC)

        Returns:
            BrokerResponse with the unchanged broker_ref
        """
        if raw.get(self._DRY_RUN_SENTINEL) == 'modify':
            return self._dry_run_simulator.modify(
                broker_ref=raw['broker_ref'],
                new_price=raw['new_price'],
                timestamp=timestamp,
            )

        return BrokerResponse(
            broker_ref=original_broker_ref,
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
            is_maker = self._maker_from_trade(trade_data)
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

    @staticmethod
    def _maker_from_trade(trade_data: Dict[str, Any]) -> bool:
        """
        Whether a fill provided liquidity — the VENUE's answer where it gives one. Pure.

        Kraken's QueryTrades carries a `maker` boolean per fill. It used to be ignored in
        favour of inferring from the ordertype, which is wrong in a way that cannot be seen
        from our side: a `stop-loss-limit` whose limit crosses the book the moment it
        triggers is a TAKER, and every one of them was booked as a maker. Our own synthetic
        fee derives maker-ness the same way, so a fee-drift audit (#327) compared a wrong
        estimate against a wrongly-parsed truth and saw agreement.

        The ordertype inference stays as the fallback for a payload that carries no flag —
        a limit-class order rests more often than not, so it is the better guess, but it
        IS a guess and the venue's own field wins.

        Args:
            trade_data: One Kraken trade record

        Returns:
            True when the fill provided liquidity
        """
        declared = trade_data.get('maker')
        if isinstance(declared, bool):
            return declared

        ordertype = str(trade_data.get('ordertype', ''))
        return (
            ordertype.startswith('limit')
            or ordertype in ('take-profit-limit', 'stop-loss-limit')
        )

    def _parse_openorders_response(self, raw: Dict[str, Any]) -> List[BrokerOrder]:
        """
        Parse Kraken OpenOrders response into List[BrokerOrder]. Pure.

        Kraken shape: {'open': {txid: {status, descr:{pair,type,ordertype,price}, vol, ...}}}.

        An ordertype this adapter cannot name becomes OrderType.UNKNOWN rather than LIMIT.
        The row is still returned — the venue reported it, and the exclusive-account check
        (#489) asks whether a stranger is working our symbol, so dropping it would hide the
        one order most worth seeing.

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
            # A MISSING ordertype used to default to 'limit', which is the same lie as the
            # unknown-type fallback and harder to notice — there is nothing to name in the
            # report either.
            kraken_ordertype = descr.get('ordertype') or ''
            status = self._STATUS_MAP.get(info.get('status', 'open'), BrokerOrderStatus.PENDING)
            price, stop_price = self._prices_from_descr(kraken_ordertype, descr)
            out.append(BrokerOrder(
                broker_ref=txid,
                symbol=self._resolve_symbol_from_pair(descr.get('pair', '')),
                direction=OrderDirection.LONG if kraken_type == 'buy' else OrderDirection.SHORT,
                order_type=self._ORDERTYPE_MAP.get(kraken_ordertype, OrderType.UNKNOWN),
                lots=float(info.get('vol', 0.0)),
                # `vol` is the ORIGINAL size; `vol_exec` is what already executed. Adoption
                # needs both, or it rebuilds a half-filled order at full size (#355).
                filled_lots=float(info.get('vol_exec', 0.0) or 0.0),
                status=status,
                price=price,
                stop_price=stop_price,
                # #473 — read our own key back. Without it a resting order we placed and a
                # resting order somebody else placed are the same unknown row.
                client_order_id=info.get('cl_ord_id') or None,
                raw=info,
            ))
        return out

    @classmethod
    def _prices_from_descr(
        cls, kraken_ordertype: str, descr: Dict[str, Any]
    ) -> Tuple[Optional[float], Optional[float]]:
        """
        Split Kraken's two price fields into a limit price and a trigger price. Pure.

        Kraken reports both in one family and their meaning depends on the ordertype:
        `descr.price` is the limit price of a `limit` order but the TRIGGER of every
        stop / take-profit type, and `descr.price2` is the limit price of the `-limit`
        variants. A trailing type reports OFFSETS in the same fields, so neither is a
        price at all and both are left unread — a trailing offset taken for a level is
        wrong by the whole distance to the market and looks entirely plausible.

        Args:
            kraken_ordertype: Raw `descr.ordertype` string as the venue reported it
            descr: The raw `descr` block

        Returns:
            (limit price or None, trigger price or None)
        """
        if kraken_ordertype in cls._OFFSET_PRICED_ORDERTYPES:
            return None, None

        primary = float(descr.get('price', 0.0) or 0.0) or None
        secondary = float(descr.get('price2', 0.0) or 0.0) or None

        if kraken_ordertype in cls._TRIGGER_PRICED_ORDERTYPES:
            return secondary, primary
        if kraken_ordertype in cls._ORDERTYPE_MAP:
            return primary, None
        # An ordertype we cannot name: the fields exist but their meaning does not.
        return None, None

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

        Raises:
            ConnectionAttemptFailedError: the request did not complete — a transport fault
                or an HTTP status. Carries whether retrying could help (§473), so the
                caller's ladder does not have to guess from a message string
        """
        if data is None:
            data = {}

        # Serialize private calls under one lock: Kraken requires a strictly
        # increasing nonce PER API key. Concurrent calls (worker thread +
        # reconciler) would otherwise collide on the same millisecond or reach
        # Kraken out of nonce order → "Invalid nonce". Holding the lock through
        # the POST guarantees unique nonces AND in-order delivery.
        with self._private_lock:
            self._enforce_rate_limit()
            headers = self._sign_request(endpoint, data)
            url = f'{self._api_base_url}{endpoint}'
            response = self._request(
                lambda: requests.post(
                    url,
                    headers=headers,
                    data=data,
                    timeout=self._request_timeout_s,
                ),
                endpoint,
            )

        result = response.json()
        # A Kraken-level error is the VENUE speaking, not a transport fault — it stays a
        # plain ConnectionError and therefore classifies TERMINAL. Retrying "Insufficient
        # funds" forever would report their outage for our order.
        errors = result.get('error', [])
        if errors:
            raise ConnectionError(f'Kraken API error: {errors}')

        return result.get('result', {})

    @staticmethod
    def _request(send: Callable[[], requests.Response], endpoint: str) -> requests.Response:
        """
        Perform one HTTP attempt and translate its failure into the shared vocabulary.

        The translation belongs here rather than in the ladder: only the transport knows
        whether it saw a dropped socket or a 400, and #473's classification is by exception
        type, never by parsing a message.

        Args:
            send: The request to make
            endpoint: API path, for the message the operator reads

        Returns:
            The response, already status-checked

        Raises:
            ConnectionAttemptFailedError: transport fault (retryable) or HTTP status
        """
        try:
            response = send()
            response.raise_for_status()
            return response
        except requests.exceptions.HTTPError as error:
            status = error.response.status_code if error.response is not None else 0
            raise ConnectionAttemptFailedError(
                f'HTTP {status} from {endpoint}',
                terminal=is_terminal_status(status)) from error
        except requests.exceptions.RequestException as error:
            raise ConnectionAttemptFailedError(
                f'{type(error).__name__} on {endpoint}: {error}', terminal=False) from error

    def _sign_request(self, url_path: str, data: Dict) -> Dict[str, str]:
        """
        Create HMAC-SHA512 signed headers for private API request.

        Args:
            url_path: API endpoint path (e.g., '/0/private/AddOrder')
            data: POST data dict (nonce is added automatically)

        Returns:
            Headers dict with API-Key and API-Sign
        """
        # Strictly increasing per API key: the time component keeps the nonce
        # above the previous session's last value (across restarts); max(.., +1)
        # guarantees a strict increase within this process (same-ms / concurrency).
        # Called only under self._private_lock, so the _last_nonce update is safe.
        nonce_int = max(int(time.time() * 1000), self._last_nonce + 1)
        self._last_nonce = nonce_int
        nonce = str(nonce_int)
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

        assert_real_credential(cred_path, 'Enabling live Kraken execution')

        with open(cred_path, 'r') as f:
            creds = json.load(f)

        api_key = creds.get('api_key', '')
        api_secret = creds.get('api_secret', '')

        if not api_key or not api_secret:
            raise ValueError(
                f"Credentials file missing 'api_key' or 'api_secret': {cred_path}"
            )

        return api_key, api_secret
