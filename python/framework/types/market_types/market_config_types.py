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
        session_bucketing: True if market has native trading sessions, False for
                          24/7 markets where time-of-day bucketing applies instead
        primary_activity_metric: Primary activity metric ('tick_count' or 'volume')
        inter_tick_gap_threshold_s: Intervals longer than this (seconds) are excluded
                                    from inter-tick profiling stats (session/weekend gaps)
    """
    weekend_closure: bool
    session_bucketing: bool
    primary_activity_metric: str
    inter_tick_gap_threshold_s: float = 300.0


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
