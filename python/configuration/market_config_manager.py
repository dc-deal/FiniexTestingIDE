"""
Market Configuration Manager
Provides lookup methods for market types and broker mappings
"""

from typing import Dict, List, Optional

from python.configuration.market_config_loader import MarketConfigFileLoader
from python.framework.types.trading_env_types.broker_types import BrokerType
from python.framework.types.config_types.market_config_types import (
    BrokerEntry,
    ConfigMode,
    MarketConfigModel,
    MarketRules,
    MarketType,
    ProfileDefaults,
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
        raw_config, _ = MarketConfigFileLoader.get_config()
        self._broker_lookup: Dict[str, BrokerEntry] = {}
        self._market_rules: Dict[MarketType, MarketRules] = {}
        self._build_lookups(raw_config)

    def _build_lookups(self, raw_config: dict) -> None:
        """Build internal lookup dictionaries from config."""
        parsed = MarketConfigModel(**raw_config)

        for market_type_str, rules in parsed.market_rules.items():
            market_type = MarketType(market_type_str)
            profile_defaults = None
            if rules.generator_profile_defaults:
                pd = rules.generator_profile_defaults
                profile_defaults = ProfileDefaults(
                    min_block_hours=pd.min_block_hours,
                    max_block_hours=pd.max_block_hours,
                    atr_percentile_threshold=pd.atr_percentile_threshold,
                )
            self._market_rules[market_type] = MarketRules(
                weekend_closure=rules.weekend_closure,
                session_bucketing=rules.session_bucketing,
                primary_activity_metric=rules.primary_activity_metric,
                inter_tick_gap_threshold_s=rules.inter_tick_gap_threshold_s,
                generator_profile_defaults=profile_defaults,
            )

        for broker in parsed.brokers:
            self._broker_lookup[broker.broker_type] = BrokerEntry(
                broker_type=broker.broker_type,
                market_type=broker.market_type,
                broker_config_path=broker.broker_config_path,
                trading_model=broker.trading_model,
                config_mode=broker.config_mode,
                credentials_file=broker.credentials_file,
                dry_run=broker.dry_run,
                api_base_url=broker.api_base_url,
                rate_limit_interval_s=broker.rate_limit_interval_s,
                request_timeout_s=broker.request_timeout_s,
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

    def get_config_mode(self, broker_type: str) -> ConfigMode:
        """
        Get config mode for a broker type.

        Args:
            broker_type: Broker type identifier

        Returns:
            ConfigMode.STATIC or ConfigMode.DYNAMIC
        """
        return self.get_broker_entry(broker_type).config_mode

    def get_dry_run(self, broker_type: str) -> bool:
        """
        Get dry_run flag for a broker type.

        Args:
            broker_type: Broker type identifier

        Returns:
            True if dry-run mode is active (no real orders placed)
        """
        return self.get_broker_entry(broker_type).dry_run

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
