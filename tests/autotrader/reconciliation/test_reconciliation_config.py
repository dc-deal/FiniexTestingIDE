"""
ReconciliationDefaults config wiring through the AutoTrader loader (#151).

Verifies the default-off behavior, profile-value application, and structural
key validation (unknown key → ValueError) via load_autotrader_config.
"""

import json

import pytest

from python.configuration.autotrader.autotrader_config_loader import load_autotrader_config


def _write_profile(tmp_path, extra: dict):
    profile = tmp_path / 'reconciliation_profile.json'
    base = {'broker_type': 'kraken_spot', 'adapter_type': 'mock'}
    base.update(extra)
    profile.write_text(json.dumps(base))
    return str(profile)


def test_reconciliation_mock_auto_disabled(tmp_path):
    # adapter_type=mock + no explicit enabled → auto-disabled (mock has no real
    # broker truth; resting orders would read as false orphans), even though the
    # app_config global default is enabled.
    config = load_autotrader_config(_write_profile(tmp_path, {}))
    assert config.reconciliation.enabled is False
    assert config.reconciliation.mode == 'alert_only'
    assert config.reconciliation.interval_ticks == 100


def test_reconciliation_live_adapter_enabled_by_default(tmp_path):
    # live adapter inherits the app_config default (enabled) — no auto-disable.
    config = load_autotrader_config(_write_profile(tmp_path, {'adapter_type': 'live'}))
    assert config.reconciliation.enabled is True


def test_reconciliation_mock_explicit_enable_overrides_auto_disable(tmp_path):
    path = _write_profile(tmp_path, {
        'reconciliation': {
            'enabled': True,
            'interval_ticks': 50,
            'min_interval_seconds': 30.0,
        }
    })
    config = load_autotrader_config(path)
    assert config.reconciliation.enabled is True  # explicit beats mock auto-disable
    assert config.reconciliation.interval_ticks == 50
    assert config.reconciliation.min_interval_seconds == 30.0


def test_reconciliation_unknown_key_rejected(tmp_path):
    path = _write_profile(tmp_path, {
        'reconciliation': {'enabled': True, 'bogus_key': 1}
    })
    with pytest.raises(ValueError):
        load_autotrader_config(path)
