"""
Market Configuration Manager
Provides lookup methods for market types and broker mappings
"""

from typing import Dict, List

from python.configuration.market_config_loader import MarketConfigFileLoader
from python.framework.types.market_config_types import (
    MarketType,
    MarketRules,
    BrokerEntry,
)


class MarketConfigManager:
    """
    Manager for market configuration lookups.

    Provides methods to:
    - Get market_type for a broker_type
    - Get MarketRules for a market_type
    - Get broker_config_path for a broker_type
    """

    def __init__(self):
        """Initialize market config manager."""
        config, _ = MarketConfigFileLoader.get_config()
        self._config = config
        self._broker_lookup: Dict[str, BrokerEntry] = {}
        self._market_rules: Dict[MarketType, MarketRules] = {}
        self._build_lookups()

    def _build_lookups(self) -> None:
        """Build internal lookup dictionaries from config."""
        # Build market rules lookup
        rules_config = self._config.get("market_rules", {})
        for market_type_str, rules_dict in rules_config.items():
            try:
                market_type = MarketType(market_type_str)
                self._market_rules[market_type] = MarketRules(
                    weekend_closure=rules_dict.get("weekend_closure", True),
                    has_trading_sessions=rules_dict.get(
                        "has_trading_sessions", True)
                )
            except ValueError:
                raise ValueError(
                    f"❌ Invalid market_type in market_config.json: '{market_type_str}'\n"
                    f"   Valid values: {[mt.value for mt in MarketType]}"
                )

        # Build broker lookup
        brokers_config = self._config.get("brokers", [])
        for broker_dict in brokers_config:
            broker_type = broker_dict.get("broker_type")
            market_type_str = broker_dict.get("market_type")
            broker_config_path = broker_dict.get("broker_config_path")

            if not broker_type:
                raise ValueError(
                    f"❌ Missing 'broker_type' in broker entry: {broker_dict}"
                )

            try:
                market_type = MarketType(market_type_str)
            except ValueError:
                raise ValueError(
                    f"❌ Invalid market_type '{market_type_str}' for broker '{broker_type}'\n"
                    f"   Valid values: {[mt.value for mt in MarketType]}"
                )

            self._broker_lookup[broker_type] = BrokerEntry(
                broker_type=broker_type,
                market_type=market_type,
                broker_config_path=broker_config_path or ""
            )

    def get_market_type(self, broker_type: str) -> MarketType:
        """
        Get market type for a broker type.

        Args:
            broker_type: Broker type identifier (e.g., 'mt5', 'kraken_spot')

        Returns:
            MarketType enum value
        """
        entry = self._broker_lookup.get(broker_type)

        if entry is None:
            available = list(self._broker_lookup.keys())
            raise ValueError(
                f"❌ Unknown broker_type: '{broker_type}'\n"
                f"   Available broker types: {available}\n"
                f"   Add this broker_type to configs/market_config.json"
            )

        return entry.market_type

    def get_market_rules(self, market_type: MarketType) -> MarketRules:
        """
        Get market rules for a market type.

        Args:
            market_type: MarketType enum value

        Returns:
            MarketRules dataclass with trading rules
        """
        rules = self._market_rules.get(market_type)

        if rules is None:
            raise ValueError(
                f"❌ No market rules defined for: '{market_type.value}'\n"
                f"   Add rules to market_config.json under 'market_rules'"
            )

        return rules

    def get_market_rules_for_broker(self, broker_type: str) -> MarketRules:
        """
        Get market rules for a broker type (convenience method).

        Args:
            broker_type: Broker type identifier

        Returns:
            MarketRules for the broker's market type
        """
        market_type = self.get_market_type(broker_type)
        return self.get_market_rules(market_type)

    def get_broker_config_path(self, broker_type: str) -> str:
        """
        Get broker config path for a broker type.

        Args:
            broker_type: Broker type identifier

        Returns:
            Path to broker configuration JSON file
        """
        entry = self._broker_lookup.get(broker_type)

        if entry is None:
            available = list(self._broker_lookup.keys())
            raise ValueError(
                f"❌ Unknown broker_type: '{broker_type}'\n"
                f"   Available: {available}"
            )

        return entry.broker_config_path

    def get_broker_entry(self, broker_type: str) -> BrokerEntry:
        """
        Get complete broker entry.

        Args:
            broker_type: Broker type identifier

        Returns:
            BrokerEntry with all broker configuration
        """
        entry = self._broker_lookup.get(broker_type)

        if entry is None:
            available = list(self._broker_lookup.keys())
            raise ValueError(
                f"❌ Unknown broker_type: '{broker_type}'\n"
                f"   Available: {available}"
            )

        return entry

    def get_all_broker_types(self) -> List[str]:
        """
        Get list of all configured broker types.

        Returns:
            List of broker type identifiers
        """
        return list(self._broker_lookup.keys())

    def has_weekend_closure(self, broker_type: str) -> bool:
        """
        Check if broker's market has weekend closure.

        Args:
            broker_type: Broker type identifier

        Returns:
            True if market closes on weekends
        """
        rules = self.get_market_rules_for_broker(broker_type)
        return rules.weekend_closure
