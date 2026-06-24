"""
FiniexTestingIDE - Market Configuration Types
Enums, dataclasses and Pydantic models for market_config.json.
"""
from enum import Enum
from typing import Dict, List, Optional
from pydantic import BaseModel


class MarketType(Enum):
    """Supported market types with distinct trading rules."""
    FOREX = "forex"
    CRYPTO = "crypto"


class TradingModel(Enum):
    """Trading model — determines balance tracking and order validation."""
    MARGIN = 'margin'
    SPOT = 'spot'


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


class ProfileDefaultsConfig(BaseModel):
    """Generator profile defaults per market type."""
    min_block_hours: int = 2
    max_block_hours: int = 24
    atr_percentile_threshold: int = 10


class SwapRolloverConfig(BaseModel):
    """
    Daily swap / overnight-funding rollover anchor for a market.

    The local wall-clock time at which the broker books the daily swap, plus the
    IANA timezone it is expressed in. Resolved per date (DST-aware) via zoneinfo.
    Present for markets that charge overnight financing (Forex); absent for spot
    markets without swap (crypto).
    """
    local_time: str = '17:00'
    timezone: str = 'America/New_York'


class MarketRulesConfig(BaseModel):
    """Market rules entry as loaded from JSON."""
    weekend_closure: bool
    session_bucketing: bool
    primary_activity_metric: str
    pip_mode: PipMode
    inter_tick_gap_threshold_s: float = 300.0
    generator_profile_defaults: Optional[ProfileDefaultsConfig] = None
    swap_rollover: Optional[SwapRolloverConfig] = None


class BrokerTransportConfig(BaseModel):
    """Per-broker transport-layer tuning (HTTP endpoint, rate limits, polling cadence)."""
    api_base_url: str = ''
    rate_limit_interval_s: float = 1.0
    request_timeout_s: int = 15
    poll_interval_ms: int = 5000


class BrokerEntryConfig(BaseModel):
    """Broker entry as loaded from JSON."""
    broker_type: str
    market_type: MarketType
    broker_config_path: str = ''
    trading_model: TradingModel = TradingModel.MARGIN
    config_mode: ConfigMode = ConfigMode.STATIC
    credentials_file: str = ''
    dry_run: bool = True
    broker_transport: BrokerTransportConfig = BrokerTransportConfig()


class MarketConfigModel(BaseModel):
    """Top-level model for market_config.json."""
    version: str
    description: str = ''
    market_rules: Dict[str, MarketRulesConfig]
    brokers: List[BrokerEntryConfig]
