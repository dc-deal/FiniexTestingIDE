"""
Activity Volume Provider
========================
Unified activity metric abstraction for different market types.

Forex uses tick_count (price changes), Crypto uses trade_volume.
This provider abstracts the difference for consistent reporting.

Location: python/framework/utils/activity_volume_provider.py
"""

from typing import Dict, Optional, Tuple


class ActivityVolumeProvider:
    """
    Provides unified activity metrics across different market types.

    Market Type Mapping:
    - forex_cfd: tick_count (number of price changes)
    - crypto_spot: trade_volume (traded volume in base currency)
    - crypto_futures: trade_volume
    - equity: trade_volume

    Usage:
        provider = ActivityVolumeProvider()
        value = provider.get_activity_value(bar_data, 'forex_cfd')
        label = provider.get_metric_label('forex_cfd')
    """

    # Market types that use trade_volume
    VOLUME_BASED_MARKETS = ['crypto_spot', 'crypto_futures', 'equity']

    # Market types that use tick_count
    TICK_BASED_MARKETS = ['forex_cfd']

    def get_activity_value(
        self,
        data: Dict,
        market_type: str
    ) -> float:
        """
        Get primary activity value for given market type.

        Args:
            data: Dict containing tick_count and/or trade_volume
            market_type: Market type string

        Returns:
            Activity value (tick_count or trade_volume)
        """
        if market_type in self.VOLUME_BASED_MARKETS:
            return float(data.get('trade_volume', 0.0) or 0.0)
        else:
            return float(data.get('tick_count', 0) or 0)

    def get_avg_activity_value(
        self,
        data: Dict,
        market_type: str
    ) -> float:
        """
        Get average activity value per bar for given market type.

        Args:
            data: Dict containing avg_ticks_per_bar and/or avg_volume_per_bar
            market_type: Market type string

        Returns:
            Average activity value per bar
        """
        if market_type in self.VOLUME_BASED_MARKETS:
            return float(data.get('avg_volume_per_bar', 0.0) or 0.0)
        else:
            return float(data.get('avg_ticks_per_bar', 0.0) or 0.0)

    def get_total_activity_value(
        self,
        data: Dict,
        market_type: str
    ) -> float:
        """
        Get total activity value for given market type.

        Args:
            data: Dict containing total_tick_count and/or total_trade_volume
            market_type: Market type string

        Returns:
            Total activity value
        """
        if market_type in self.VOLUME_BASED_MARKETS:
            return float(data.get('total_trade_volume', 0.0) or 0.0)
        else:
            return float(data.get('total_tick_count', 0) or 0)

    def get_metric_label(self, market_type: str) -> str:
        """
        Get human-readable label for the activity metric.

        Args:
            market_type: Market type string

        Returns:
            Label string (e.g., "Ticks", "Volume")
        """
        if market_type in self.VOLUME_BASED_MARKETS:
            return "Volume"
        else:
            return "Ticks"

    def get_metric_name(self, market_type: str) -> str:
        """
        Get technical field name for the activity metric.

        Args:
            market_type: Market type string

        Returns:
            Field name (e.g., "tick_count", "trade_volume")
        """
        if market_type in self.VOLUME_BASED_MARKETS:
            return "trade_volume"
        else:
            return "tick_count"

    def get_activity_summary(
        self,
        data: Dict,
        market_type: str
    ) -> Tuple[str, float, float]:
        """
        Get complete activity summary for display.

        Args:
            data: Dict with activity data
            market_type: Market type string

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
        market_type: str
    ) -> str:
        """
        Format activity value for display.

        Args:
            value: Activity value
            market_type: Market type string

        Returns:
            Formatted string
        """
        if market_type in self.VOLUME_BASED_MARKETS:
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

    def is_volume_based(self, market_type: str) -> bool:
        """
        Check if market type uses trade volume.

        Args:
            market_type: Market type string

        Returns:
            True if volume-based, False if tick-based
        """
        return market_type in self.VOLUME_BASED_MARKETS


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
