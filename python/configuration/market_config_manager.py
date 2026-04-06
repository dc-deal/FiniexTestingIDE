"""
Market Configuration Manager
Provides lookup methods for market types and broker mappings
"""

from typing import Dict, List, Optional

from python.configuration.market_config_loader import MarketConfigFileLoader
from python.framework.types.trading_env_types.broker_types import BrokerType
from python.framework.types.market_types.market_config_types import (
    MarketType,
    MarketRules,
    ProfileDefaults,
    BrokerEntry,
    TradingModel,
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
                # Parse profile defaults if present
                profile_defaults = None
                profile_dict = rules_dict.get("generator_profile_defaults")
                if profile_dict:
                    profile_defaults = ProfileDefaults(
                        min_block_hours=profile_dict.get("min_block_hours", 2),
                        max_block_hours=profile_dict.get("max_block_hours", 24),
                        atr_percentile_threshold=profile_dict.get("atr_percentile_threshold", 10),
                    )

                self._market_rules[market_type] = MarketRules(
                    weekend_closure=rules_dict.get("weekend_closure", True),
                    session_bucketing=rules_dict.get(
                        "session_bucketing", True),
                    primary_activity_metric=rules_dict.get(
                        "primary_activity_metric", "tick_count"),
                    inter_tick_gap_threshold_s=rules_dict.get(
                        "inter_tick_gap_threshold_s", 300.0),
                    generator_profile_defaults=profile_defaults,
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

            # Parse trading_model (default: margin)
            trading_model_str = broker_dict.get('trading_model', 'margin')
            try:
                trading_model = TradingModel(trading_model_str)
            except ValueError:
                raise ValueError(
                    f"❌ Invalid trading_model '{trading_model_str}' for broker '{broker_type}'\n"
                    f"   Valid values: {[tm.value for tm in TradingModel]}"
                )

            self._broker_lookup[broker_type] = BrokerEntry(
                broker_type=broker_type,
                market_type=market_type,
                broker_config_path=broker_config_path or "",
                trading_model=trading_model,
            )

    def get_market_type(self, broker_type: BrokerType) -> MarketType:
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

    def get_trading_model(self, broker_type: str) -> TradingModel:
        """
        Get trading model for a broker type.

        Args:
            broker_type: Broker type identifier

        Returns:
            TradingModel enum value (MARGIN or SPOT)
        """
        entry = self.get_broker_entry(broker_type)
        return entry.trading_model

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

    def get_primary_activity_metric(self, market_type: MarketType) -> str:
        """
        Get primary activity metric for a market type.

        Args:
            market_type: MarketType enum value

        Returns:
            Activity metric string ('tick_count' or 'volume')
        """
        rules = self.get_market_rules(market_type)
        return rules.primary_activity_metric

    def get_primary_activity_metric_for_broker(self, broker_type: str) -> str:
        """
        Get primary activity metric for a broker type (convenience method).

        Args:
            broker_type: Broker type identifier

        Returns:
            Activity metric string ('tick_count' or 'volume')
        """
        market_type = self.get_market_type(broker_type)
        return self.get_primary_activity_metric(market_type)

    def get_generator_profile_defaults_for_broker(self, broker_type: str) -> Optional[ProfileDefaults]:
        """
        Get generator profile defaults for a broker type.

        Args:
            broker_type: Broker type identifier

        Returns:
            ProfileDefaults or None if not configured
        """
        rules = self.get_market_rules_for_broker(broker_type)
        return rules.generator_profile_defaults
