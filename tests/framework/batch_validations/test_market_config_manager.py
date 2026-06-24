"""
FiniexTestingIDE - MarketConfigManager Unit Tests

Covers:
- ConfigMode parsing from market_config.json broker entries
- Static default when config_mode is omitted
- Invalid config_mode raises ValidationError
- get_config_mode() getter returns correct enum value
- Unknown broker_type raises ValueError
"""

import pytest
from pydantic import ValidationError
from unittest.mock import patch

from python.configuration.market_config_manager import MarketConfigManager
from python.framework.types.config_types.market_config_types import ConfigMode


_CONFIG_STATIC_AND_DYNAMIC = {
    'version': '1.0',
    'market_rules': {
        'forex': {
            'weekend_closure': True,
            'session_bucketing': True,
            'primary_activity_metric': 'tick_count',
            'pip_mode': 'fractional_pip',
        },
        'crypto': {
            'weekend_closure': False,
            'session_bucketing': False,
            'primary_activity_metric': 'volume',
            'pip_mode': 'tick',
        },
    },
    'brokers': [
        {
            'broker_type': 'mt5_forex',
            'market_type': 'forex',
            'broker_config_path': 'configs/brokers/mt5/mt5_forex_broker_config.json',
            'trading_model': 'margin',
        },
        {
            'broker_type': 'kraken_spot',
            'market_type': 'crypto',
            'broker_config_path': 'configs/brokers/kraken/kraken_spot_broker_config.json',
            'trading_model': 'spot',
            'config_mode': 'dynamic',
        },
    ],
}


def _make_manager(config: dict) -> MarketConfigManager:
    with patch(
        'python.configuration.market_config_manager.MarketConfigFileLoader.get_config',
        return_value=(config, True),
    ):
        return MarketConfigManager()


class TestConfigModeParsing:
    """ConfigMode field — parsing from broker entry dict."""

    def test_dynamic_mode_parsed_correctly(self):
        manager = _make_manager(_CONFIG_STATIC_AND_DYNAMIC)
        assert manager.get_config_mode('kraken_spot') == ConfigMode.DYNAMIC

    def test_static_default_when_omitted(self):
        # mt5_forex entry has no config_mode field — must default to STATIC
        manager = _make_manager(_CONFIG_STATIC_AND_DYNAMIC)
        assert manager.get_config_mode('mt5_forex') == ConfigMode.STATIC

    def test_invalid_config_mode_raises(self):
        config = {
            'version': '1.0',
            'market_rules': {
                'forex': {
                    'weekend_closure': True,
                    'session_bucketing': True,
                    'primary_activity_metric': 'tick_count',
                    'pip_mode': 'fractional_pip',
                },
            },
            'brokers': [
                {
                    'broker_type': 'mt5_forex',
                    'market_type': 'forex',
                    'broker_config_path': 'configs/brokers/mt5/mt5_forex_broker_config.json',
                    'config_mode': 'turbo',
                },
            ],
        }
        with pytest.raises(ValidationError, match="config_mode"):
            _make_manager(config)

    def test_get_config_mode_getter(self):
        manager = _make_manager(_CONFIG_STATIC_AND_DYNAMIC)
        result = manager.get_config_mode('kraken_spot')
        assert result == ConfigMode.DYNAMIC
        assert isinstance(result, ConfigMode)

    def test_unknown_broker_raises(self):
        manager = _make_manager(_CONFIG_STATIC_AND_DYNAMIC)
        with pytest.raises(ValueError, match="Unknown broker_type"):
            manager.get_config_mode('unknown_broker')
