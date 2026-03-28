"""
FiniexTestingIDE - Abstract Broker Config Fetcher
Interface for fetching broker configuration from live APIs.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional


class AbstractBrokerConfigFetcher(ABC):
    """
    Abstract interface for fetching broker config from a live API.

    Implementations fetch symbol specs and account info at startup,
    producing a config dict compatible with BrokerConfigFactory.from_serialized_dict().

    Args:
        credentials_path: Path to credentials JSON file
    """

    @abstractmethod
    def fetch_broker_config(self, symbol: str, broker_type: str) -> Dict[str, Any]:
        """
        Fetch broker config dict for a single symbol.

        Args:
            symbol: Trading symbol (e.g., 'BTCUSD')
            broker_type: Broker type identifier (e.g., 'kraken_spot')

        Returns:
            Complete broker config dict (same structure as static JSON)
        """

    @abstractmethod
    def fetch_account_balance(self, currency: str) -> Optional[float]:
        """
        Fetch current account balance for a currency.

        Args:
            currency: Account currency code (e.g., 'USD')

        Returns:
            Account balance, or None if unavailable
        """
