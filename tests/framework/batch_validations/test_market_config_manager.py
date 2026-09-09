"""
FiniexTestingIDE - MarketConfigManager Unit Tests

Covers:
- ConfigMode parsing from market_config.json broker entries
- Static default when config_mode is omitted
- Invalid config_mode raises ValidationError
- get_config_mode() getter returns correct enum value
- Unknown broker_type raises ValueError
"""

from unittest.mock import patch

import pytest
from pydantic import ValidationError

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
        with pytest.raises(ValidationError, match='config_mode'):
            _make_manager(config)

    def test_get_config_mode_getter(self):
        manager = _make_manager(_CONFIG_STATIC_AND_DYNAMIC)
        result = manager.get_config_mode('kraken_spot')
        assert result == ConfigMode.DYNAMIC
        assert isinstance(result, ConfigMode)

    def test_unknown_broker_raises(self):
        manager = _make_manager(_CONFIG_STATIC_AND_DYNAMIC)
        with pytest.raises(ValueError, match='Unknown broker_type'):
            manager.get_config_mode('unknown_broker')


class TestATypoInMarketConfigIsRefused:
    """
    `market_config.json` had no unknown-key guard of any kind — and it is the file that
    carries the real-money posture.

    `check_unknown_keys` is called for the AutoTrader profile and the scenario set, never for
    this file, and the models did not forbid extras. So a misspelled key was silently dropped
    in the one place that declares `dry_run`, `credentials_file` and `session_end_orders`: a
    posture setting reading as ABSENT because of a typo, with nothing to say so. The §28 guard
    test covers only the profile lane, so nothing else would catch it either.
    """

    def _entry(self, **overrides) -> dict:
        """
        A minimal broker entry, optionally with extra or misspelled keys.

        Args:
            overrides: Keys to add or replace on the entry

        Returns:
            The broker entry dict
        """
        entry = {'broker_type': 'kraken_spot', 'market_type': 'crypto'}
        entry.update(overrides)
        return entry

    def _config(self, entry: dict) -> dict:
        return {
            'version': '1.0',
            'market_rules': {
                'crypto': {
                    'weekend_closure': False, 'session_bucketing': False,
                    'primary_activity_metric': 'volume', 'pip_mode': 'tick',
                },
            },
            'brokers': [entry],
        }

    def test_a_misspelled_posture_key_raises_instead_of_vanishing(self):
        config = self._config(self._entry(dry_runn=False))

        with pytest.raises(ValidationError):
            _make_manager(config)

    def test_an_unknown_key_in_a_nested_block_raises_too(self):
        """The guard has to reach the transport block, not just the entry."""
        config = self._config(self._entry(
            broker_transport={'api_base_url': 'https://x', 'rate_limit_intervall_s': 1.0}))

        with pytest.raises(ValidationError):
            _make_manager(config)

    def test_the_guard_reaches_the_connection_block_two_levels_down(self):
        """
        The deepest block in the file, and the one where a typo is worst.

        `attempt_budget: 0` means NEVER GIVE UP (§43). A misspelled key there reads as absent,
        so the ladder silently runs the default instead of the operator's number — in the
        block that decides how a real-money session behaves when the venue stops answering.
        The strict base stopped one level above this until the guard was shared.
        """
        config = self._config(self._entry(broker_transport={
            'api_base_url': 'https://x',
            'connection': {'initial_delay_s': 1.0, 'attempt_budgett': 9},
        }))

        with pytest.raises(ValidationError):
            _make_manager(config)

    def test_a_comment_still_explains_the_file(self):
        """
        §28 makes `_comment` the way a config file documents itself, and the whitelist comes
        from the same helper `check_unknown_keys` honours — so the two cannot drift apart.
        """
        config = self._config(self._entry(_comment='why this broker is configured this way'))
        config['_comment'] = 'top level too'

        manager = _make_manager(config)

        assert manager.get_broker_entry('kraken_spot').broker_type == 'kraken_spot'

    def test_the_shipped_config_still_loads_and_mirrors_its_defaults(self):
        """
        The mirror check §28 asks for: a field declared in the model must be reachable from
        the real JSON. Nothing else covers `market_config.json` — the loader-coverage test
        walks `AutoTraderConfig` only.
        """
        entry = MarketConfigManager().get_broker_entry('kraken_spot')

        assert entry.config_mode is ConfigMode.DYNAMIC
        assert entry.credentials_file, 'declared in the model and set in the shipped JSON'
