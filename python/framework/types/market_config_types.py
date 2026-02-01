"""
Market Configuration Types
Type definitions for market rules and broker mappings
"""

from dataclasses import dataclass
from enum import Enum
from typing import Dict, List


class MarketType(Enum):
    """Supported market types with distinct trading rules."""
    FOREX = "forex"
    CRYPTO = "crypto"


@dataclass
class MarketRules:
    """
    Trading rules for a specific market type.

    Args:
        weekend_closure: True if market closes on weekends
        has_trading_sessions: True if market has distinct trading sessions
        primary_activity_metric: Primary activity metric ('tick_count' or 'volume')
    """
    weekend_closure: bool
    has_trading_sessions: bool
    primary_activity_metric: str


@dataclass
class BrokerEntry:
    """
    Broker configuration entry mapping broker_type to market_type.

    Args:
        broker_type: Unique identifier for data source (e.g., 'mt5', 'kraken_spot')
        market_type: Associated market type for trading rules
        broker_config_path: Path to broker JSON configuration file
    """
    broker_type: str
    market_type: MarketType
    broker_config_path: str


@dataclass
class MarketConfig:
    """
    Complete market configuration.

    Args:
        version: Configuration version string
        description: Human-readable description
        market_rules: Dict mapping MarketType to MarketRules
        brokers: List of broker entries
    """
    version: str
    description: str
    market_rules: Dict[MarketType, MarketRules]
    brokers: List[BrokerEntry]
