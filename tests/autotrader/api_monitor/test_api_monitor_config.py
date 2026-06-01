"""
ApiMonitorConfig wiring through the AutoTrader loader (#351).

Default ON for live, auto-disabled for mock (unless explicitly set), structural
key validation. Mirrors the reconciliation config test pattern.
"""

import json

import pytest

from python.configuration.autotrader.autotrader_config_loader import load_autotrader_config


def _write_profile(tmp_path, extra: dict):
    profile = tmp_path / 'api_monitor_profile.json'
    base = {'broker_type': 'kraken_spot', 'adapter_type': 'mock'}
    base.update(extra)
    profile.write_text(json.dumps(base))
    return str(profile)


def test_api_monitor_mock_auto_disabled(tmp_path):
    config = load_autotrader_config(_write_profile(tmp_path, {}))
    assert config.api_monitor.enabled is False
    assert config.api_monitor.slow_call_threshold_ms == 3000.0


def test_api_monitor_live_enabled_by_default(tmp_path):
    config = load_autotrader_config(_write_profile(tmp_path, {'adapter_type': 'live'}))
    assert config.api_monitor.enabled is True


def test_api_monitor_mock_explicit_enable_overrides(tmp_path):
    path = _write_profile(tmp_path, {
        'api_monitor': {'enabled': True, 'slow_call_threshold_ms': 1500.0}
    })
    config = load_autotrader_config(path)
    assert config.api_monitor.enabled is True
    assert config.api_monitor.slow_call_threshold_ms == 1500.0


def test_api_monitor_unknown_key_rejected(tmp_path):
    path = _write_profile(tmp_path, {'api_monitor': {'enabled': True, 'bogus_key': 1}})
    with pytest.raises(ValueError):
        load_autotrader_config(path)
