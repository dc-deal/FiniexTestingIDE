"""
FiniexTestingIDE - Market Configuration Types
Enums, dataclasses and Pydantic models for market_config.json.
"""
from dataclasses import dataclass
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


@dataclass
class ProfileDefaults:
    """
    Default profile generation parameters per market type.

    Args:
        min_block_hours: Minimum block duration in hours
        max_block_hours: Maximum block duration in hours
        atr_percentile_threshold: ATR percentile for split point detection
    """
    min_block_hours: int = 2
    max_block_hours: int = 24
    atr_percentile_threshold: int = 10


@dataclass
class MarketRules:
    """
    Trading rules for a specific market type.

    Args:
        weekend_closure: True if market closes on weekends
        session_bucketing: True if market has native trading sessions, False for
                          24/7 markets where time-of-day bucketing applies instead
        primary_activity_metric: Primary activity metric ('tick_count' or 'volume')
        inter_tick_gap_threshold_s: Intervals longer than this (seconds) are excluded
                                    from inter-tick profiling stats (session/weekend gaps)
        generator_profile_defaults: Default profile generation parameters for this market type
    """
    weekend_closure: bool
    session_bucketing: bool
    primary_activity_metric: str
    inter_tick_gap_threshold_s: float = 300.0
    generator_profile_defaults: Optional[ProfileDefaults] = None


@dataclass
class BrokerEntry:
    """
    Broker configuration entry mapping broker_type to market_type.

    Args:
        broker_type: Unique identifier for data source (e.g., 'mt5', 'kraken_spot')
        market_type: Associated market type for trading rules
        broker_config_path: Path to broker JSON configuration file (static seed)
        trading_model: Trading model (margin or spot) — determines balance tracking
        config_mode: Static file (default) or API-fetched runtime cache
        credentials_file: Credentials filename for live API access (cascade: user_configs/ → configs/)
        dry_run: True = validate only, no real orders placed (global default per broker)
        api_base_url: Broker API base URL (empty = use fetcher default)
        rate_limit_interval_s: Minimum seconds between API requests
        request_timeout_s: HTTP request timeout in seconds
    """
    broker_type: str
    market_type: MarketType
    broker_config_path: str
    # MULTI-SYMBOL TOUCHPOINT
    # Spot mode changes "margin aggregation" (#257 Phase 2) to "shared currency pool"
    trading_model: TradingModel = TradingModel.MARGIN
    config_mode: ConfigMode = ConfigMode.STATIC
    credentials_file: str = ''
    dry_run: bool = True
    api_base_url: str = ''
    rate_limit_interval_s: float = 1.0
    request_timeout_s: int = 15


# ============================================
# Pydantic Parsing Models (market_config.json)
# ============================================

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
