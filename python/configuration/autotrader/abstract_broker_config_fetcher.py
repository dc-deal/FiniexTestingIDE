"""
FiniexTestingIDE - Abstract Broker Config Fetcher
Interface for fetching broker configuration from live APIs.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from python.framework.types.trading_env_types.broker_types import FeeTierInfo


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

    def fetch_fee_tier(self, symbol: str) -> Optional[FeeTierInfo]:
        """
        Fetch the fee schedule this ACCOUNT is on for a symbol (#337).

        Capability by override, with no flag and no isinstance check: a venue that prices per
        account volume answers, everything else keeps this default and the caller needs no
        branch. A spread broker has no tier to report, so MT5 will not implement it (#209).

        Deliberately NOT abstract — making it so would force every fetcher to write a method
        for a question its venue does not ask.

        Args:
            symbol: Trading symbol (e.g., 'ETHUSD')

        Returns:
            The account's current rates, or None when this venue has no account tier or the
            answer could not be obtained — the caller keeps its configured rates either way
        """
        return None

    @abstractmethod
    def fetch_account_balance(self, currency: str) -> Optional[float]:
        """
        Fetch current account balance for a currency.

        Args:
            currency: Account currency code (e.g., 'USD')

        Returns:
            Account balance, or None if unavailable
        """
