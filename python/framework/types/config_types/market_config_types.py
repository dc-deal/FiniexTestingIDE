"""
FiniexTestingIDE - Market Configuration Types
Enums, dataclasses and Pydantic models for market_config.json.
"""
from enum import Enum
from typing import Dict, List, Literal, Optional

from python.framework.types.config_types.connection_policy_config_types import (
    ConnectionPolicy,
)
from python.framework.types.config_types.strict_config_model import StrictConfigModel


class MarketType(Enum):
    """Supported market types with distinct trading rules."""
    FOREX = 'forex'
    CRYPTO = 'crypto'


class TradingModel(Enum):
    """Trading model — determines balance tracking and order validation."""
    MARGIN = 'margin'
    SPOT = 'spot'


class PriceFormation(Enum):
    """
    How prices come about at a venue — which decides whether a traded price exists.

    ORDER_DRIVEN — a central limit order book matches buy against sell orders, so every
        trade PRINTS at one price. `last` is a real event, real per-trade volume exists,
        and the venue's own charts are built from trades (Kraken spot, futures venues,
        exchange-traded stocks).
    QUOTE_DRIVEN — a dealer quotes a bid and an ask and takes the other side itself. There
        is no central place where trades happen, so there is no "last traded price" at all:
        MT5 forex reports `last = 0.0` on 100 % of ticks, which is not a gap but an absence.

    The vocabulary is Larry Harris, Trading and Exchanges, ch. 5-6.

    It is a property of the VENUE, not of the asset class and not of the account model — a
    crypto CFD at an MT5 broker is `market_type: crypto`, `trading_model: margin` and
    QUOTE_DRIVEN. The three axes are independent; see docs/architecture/market_model.md.

    The project already carried this distinction twice before it had a name:
    `market_rules.<type>.primary_activity_metric` (volume where trades print, tick_count
    where they do not) and the collector EA's gate on SYMBOL_CALC_MODE_EXCH_* for real
    volume. Real volume and a traded price are siblings.
    """
    ORDER_DRIVEN = 'order_driven'
    QUOTE_DRIVEN = 'quote_driven'


class PipMode(Enum):
    """
    How a market's 'pip' price unit is derived from the broker tick / digits.

    FRACTIONAL_PIP — Forex convention: a pip is the 4th decimal (2nd for JPY).
        Fractional-pip ('pipette') brokers quote one extra digit (5-digit / 3-digit
        JPY), so pip = tick * 10; whole-pip brokers (4-/2-digit) use pip = tick.
    TICK — no pip concept (crypto / others): the broker tick IS the price unit.
    """
    FRACTIONAL_PIP = 'fractional_pip'
    TICK = 'tick'

    @property
    def unit_label(self) -> str:
        """Human report unit label for this mode ('pip' / 'tick')."""
        return 'pip' if self is PipMode.FRACTIONAL_PIP else 'tick'


class ConfigMode(Enum):
    """Broker config source — static file vs API-fetched runtime cache."""
    STATIC = 'static'
    DYNAMIC = 'dynamic'


class ProfileDefaultsConfig(StrictConfigModel):
    """Generator profile defaults per market type."""
    min_block_hours: int = 2
    max_block_hours: int = 24
    atr_percentile_threshold: int = 10


class SwapRolloverConfig(StrictConfigModel):
    """
    Daily swap / overnight-funding rollover anchor for a market.

    The local wall-clock time at which the broker books the daily swap, plus the
    IANA timezone it is expressed in. Resolved per date (DST-aware) via zoneinfo.
    Present for markets that charge overnight financing (Forex); absent for spot
    markets without swap (crypto).
    """
    local_time: str = '17:00'
    timezone: str = 'America/New_York'


class MarketRulesConfig(StrictConfigModel):
    """Market rules entry as loaded from JSON."""
    weekend_closure: bool
    session_bucketing: bool
    primary_activity_metric: str
    pip_mode: PipMode
    inter_tick_gap_threshold_s: float = 300.0
    generator_profile_defaults: Optional[ProfileDefaultsConfig] = None
    swap_rollover: Optional[SwapRolloverConfig] = None


class BrokerTransportConfig(StrictConfigModel):
    """Per-broker transport-layer tuning (HTTP endpoint, rate limits, polling cadence)."""
    api_base_url: str = ''
    rate_limit_interval_s: float = 1.0
    request_timeout_s: int = 15
    poll_interval_ms: int = 5000
    # #473 — the retry ladder and give-up rule for every call to THIS broker: the REST
    # endpoint, the warmup bar history and the configuration read. One schema, shared with
    # the producer's and the tick source's own blocks.
    connection: ConnectionPolicy = ConnectionPolicy()


class BrokerEntryConfig(StrictConfigModel):
    """Broker entry as loaded from JSON."""
    broker_type: str
    market_type: MarketType
    # How this VENUE forms its prices — deliberately REQUIRED, with no default. A default is
    # how the next broker silently inherits the wrong bar basis, which is the defect this
    # field exists to remove; without one, StrictConfigModel refuses the load instead.
    price_formation: PriceFormation
    broker_config_path: str = ''
    trading_model: TradingModel = TradingModel.MARGIN
    config_mode: ConfigMode = ConfigMode.STATIC
    credentials_file: str = ''
    dry_run: bool = True
    # The broker's standing posture on leaving resting orders behind after a session ends
    # (#492). A profile may TIGHTEN this to 'cancel' freely and may only choose 'leave'
    # when this says so — afterwards orders sit at a venue with nobody watching, and a
    # profile is the most easily copied file in the project. Same asymmetry as dry_run.
    session_end_orders: Literal['cancel', 'leave'] = 'cancel'
    # #337 — APPLY the fee tier the venue reports for this ACCOUNT, instead of pricing the
    # session from the rate written into the broker config. It does NOT gate the ASK: a live
    # session always asks, because the answer is what produces the divergence warning, and a
    # seed nobody is told about is a seed that silently rots. Off, the warning still fires
    # and the declared rates stand.
    # LIVE ONLY by construction: the fetcher runs on the AutoTrader path, and a BACKTEST
    # deliberately reads its rates from the git-tracked seed so a run stays reproducible from
    # a commit (see BrokerDataPreparator). Opt-in, because turning a declared number into a
    # fetched one should be a decision — a live/dry-run asymmetry belongs in the resolver,
    # never in a second default.
    auto_detect_fee_tier: bool = False
    broker_transport: BrokerTransportConfig = BrokerTransportConfig()


class MarketConfigModel(StrictConfigModel):
    """Top-level model for market_config.json."""
    version: str
    description: str = ''
    market_rules: Dict[str, MarketRulesConfig]
    brokers: List[BrokerEntryConfig]
