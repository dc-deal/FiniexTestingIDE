"""
FiniexTestingIDE - Market Compatibility Tests — Activity Metric Lookup

Verifies that MarketConfigManager resolves the broker → activity metric
relationship correctly. Uses the real market_config.json so the test
breaks if forex/crypto metric assignments ever drift.
"""

import pytest


def test_forex_broker_returns_tick_count(market_config_manager):
    """MT5 (forex) → tick_count metric."""
    metric = market_config_manager.get_primary_activity_metric_for_broker('mt5')
    assert metric == 'tick_count'


def test_crypto_broker_returns_volume(market_config_manager):
    """Kraken spot (crypto) → volume metric."""
    metric = market_config_manager.get_primary_activity_metric_for_broker('kraken_spot')
    assert metric == 'volume'


def test_unknown_broker_raises_value_error(market_config_manager):
    """Unknown broker string must raise ValueError."""
    with pytest.raises(ValueError):
        market_config_manager.get_primary_activity_metric_for_broker('does_not_exist')
