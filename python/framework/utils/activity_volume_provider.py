"""
Activity Volume Provider
========================
Unified activity metric abstraction for different market types.

Forex uses tick_count (price changes), Crypto uses volume.
This provider abstracts the difference for consistent reporting.

"""

from typing import Dict, Optional, Tuple, Union

from python.configuration.market_config_manager import MarketConfigManager
from python.framework.types.market_config_types import MarketType


class ActivityVolumeProvider:
    """
    Provides unified activity metrics across different market types.

    Market Type Mapping (via MarketConfigManager):
    - forex: tick_count (number of price changes)
    - crypto: volume (traded volume in base currency)

    Usage:
        provider = ActivityVolumeProvider()
        value = provider.get_activity_value(bar_data, MarketType.FOREX)
        label = provider.get_metric_label(MarketType.CRYPTO)
    """

    def __init__(self):
        """Initialize provider with MarketConfigManager."""
        self._market_config = MarketConfigManager()

    def _resolve_market_type(self, market_type: Union[MarketType, str]) -> MarketType:
        """
        Resolve market_type input to MarketType enum.

        Supports both MarketType enum and string inputs for backwards compatibility.

        Args:
            market_type: MarketType enum or string

        Returns:
            MarketType enum
        """
        if isinstance(market_type, MarketType):
            return market_type

        # String input - try to match
        market_type_lower = market_type.lower()

        # Direct match
        try:
            return MarketType(market_type_lower)
        except ValueError:
            raise ValueError(
                f"❌ No MarketType defined for: '{market_type}'\n"
                f"   Add market_type & config to market_config.json"
            )

    def _is_volume_based(self, market_type: Union[MarketType, str]) -> bool:
        """
        Check if market type uses trade volume as primary metric.

        Args:
            market_type: MarketType enum or string

        Returns:
            True if volume-based, False if tick-based
        """
        resolved = self._resolve_market_type(market_type)
        rules = self._market_config.get_market_rules(resolved)
        return rules.primary_activity_metric == 'volume'

    def get_activity_value(
        self,
        data: Dict,
        market_type: Union[MarketType, str]
    ) -> float:
        """
        Get primary activity value for given market type.

        Args:
            data: Dict containing tick_count and/or volume
            market_type: MarketType enum or string

        Returns:
            Activity value (tick_count or volume)
        """
        if self._is_volume_based(market_type):
            return float(data.get('volume', 0.0) or 0.0)
        else:
            return float(data.get('tick_count', 0) or 0)

    def get_avg_activity_value(
        self,
        data: Dict,
        market_type: Union[MarketType, str]
    ) -> float:
        """
        Get average activity value per bar for given market type.

        Args:
            data: Dict containing avg_ticks_per_bar and/or avg_volume_per_bar
            market_type: MarketType enum or string

        Returns:
            Average activity value per bar
        """
        if self._is_volume_based(market_type):
            return float(data.get('avg_volume_per_bar', 0.0) or 0.0)
        else:
            return float(data.get('avg_ticks_per_bar', 0.0) or 0.0)

    def get_total_activity_value(
        self,
        data: Dict,
        market_type: Union[MarketType, str]
    ) -> float:
        """
        Get total activity value for given market type.

        Args:
            data: Dict containing total_tick_count and/or total_trade_volume
            market_type: MarketType enum or string

        Returns:
            Total activity value
        """
        if self._is_volume_based(market_type):
            return float(data.get('total_trade_volume', 0.0) or 0.0)
        else:
            return float(data.get('total_tick_count', 0) or 0)

    def get_metric_label(self, market_type: Union[MarketType, str]) -> str:
        """
        Get human-readable label for the activity metric.

        Args:
            market_type: MarketType enum or string

        Returns:
            Label string (e.g., "Ticks", "Volume")
        """
        if self._is_volume_based(market_type):
            return "Volume"
        else:
            return "Ticks"

    def get_metric_name(self, market_type: Union[MarketType, str]) -> str:
        """
        Get technical field name for the activity metric.

        Args:
            market_type: MarketType enum or string

        Returns:
            Field name (e.g., "tick_count", "volume")
        """
        if self._is_volume_based(market_type):
            return "volume"
        else:
            return "tick_count"

    def get_activity_summary(
        self,
        data: Dict,
        market_type: Union[MarketType, str]
    ) -> Tuple[str, float, float]:
        """
        Get complete activity summary for display.

        Args:
            data: Dict with activity data
            market_type: MarketType enum or string

        Returns:
            Tuple of (label, total_value, avg_value)
        """
        label = self.get_metric_label(market_type)
        total = self.get_total_activity_value(data, market_type)
        avg = self.get_avg_activity_value(data, market_type)

        return (label, total, avg)

    def format_activity_value(
        self,
        value: float,
        market_type: Union[MarketType, str]
    ) -> str:
        """
        Format activity value for display.

        Args:
            value: Activity value
            market_type: MarketType enum or string

        Returns:
            Formatted string
        """
        if self._is_volume_based(market_type):
            # Volume: show with decimals
            if value >= 1_000_000:
                return f"{value/1_000_000:.2f}M"
            elif value >= 1_000:
                return f"{value/1_000:.2f}K"
            else:
                return f"{value:.2f}"
        else:
            # Ticks: show as integer with thousands separator
            return f"{int(value):,}"

    def is_volume_based(self, market_type: Union[MarketType, str]) -> bool:
        """
        Check if market type uses trade volume.

        Args:
            market_type: MarketType enum or string

        Returns:
            True if volume-based, False if tick-based
        """
        return self._is_volume_based(market_type)


# Singleton instance for convenience
_provider_instance: Optional[ActivityVolumeProvider] = None


def get_activity_provider() -> ActivityVolumeProvider:
    """
    Get singleton ActivityVolumeProvider instance.

    Returns:
        ActivityVolumeProvider instance
    """
    global _provider_instance
    if _provider_instance is None:
        _provider_instance = ActivityVolumeProvider()
    return _provider_instance
