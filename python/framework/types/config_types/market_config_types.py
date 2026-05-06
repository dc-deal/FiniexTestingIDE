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


class ConfigMode(Enum):
    """Broker config source — static file vs API-fetched runtime cache."""
    STATIC = 'static'
    DYNAMIC = 'dynamic'


class ProfileDefaultsConfig(BaseModel):
    """Generator profile defaults per market type."""
    min_block_hours: int = 2
    max_block_hours: int = 24
    atr_percentile_threshold: int = 10


class MarketRulesConfig(BaseModel):
    """Market rules entry as loaded from JSON."""
    weekend_closure: bool
    session_bucketing: bool
    primary_activity_metric: str
    inter_tick_gap_threshold_s: float = 300.0
    generator_profile_defaults: Optional[ProfileDefaultsConfig] = None


class BrokerEntryConfig(BaseModel):
    """Broker entry as loaded from JSON."""
    broker_type: str
    market_type: MarketType
    broker_config_path: str = ''
    trading_model: TradingModel = TradingModel.MARGIN
    config_mode: ConfigMode = ConfigMode.STATIC
    credentials_file: str = ''
    dry_run: bool = True
    api_base_url: str = ''
    rate_limit_interval_s: float = 1.0
    request_timeout_s: int = 15


class MarketConfigModel(BaseModel):
    """Top-level model for market_config.json."""
    version: str
    description: str = ''
    market_rules: Dict[str, MarketRulesConfig]
    brokers: List[BrokerEntryConfig]
