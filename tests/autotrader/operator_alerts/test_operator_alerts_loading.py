"""
Operator Alerts Loading — a mock session does not page the operator unless its profile says so.

Switched on in `user_configs/app_config.json`, the alerts would otherwise reach every mock
replay — a test run, a smoke-protocol entry that ends FAILED by design — and the ping would feed
the live bot's monitor check. Same provenance rule as the drift audit and the reconciliation: a
profile that sets `enabled` keeps its own answer.
"""

import json
from copy import deepcopy

from python.configuration.app_config_manager import AppConfigManager
from python.configuration.autotrader.autotrader_config_loader import load_autotrader_config

MOCK_PROFILE = 'configs/autotrader_profiles/mock/diagnostics_probe_test.json'


def _profile_copy(tmp_path, **overrides) -> str:
    """A copy of a mock profile outside the fixture folder, with overrides on top."""
    profile = json.loads(open(MOCK_PROFILE).read())
    profile.pop('run_purpose', None)           # a profile outside configs/ declares none
    profile.update(overrides)
    path = tmp_path / 'mock_with_alerts.json'
    path.write_text(json.dumps(profile))
    return str(path)


def test_a_mock_session_switches_both_off(monkeypatch):
    """Both switched on in the defaults — the layer user_configs/app_config.json overrides."""
    switched_on = deepcopy(AppConfigManager().get_autotrader_defaults())
    switched_on['operator_alerts']['telegram']['enabled'] = True
    switched_on['operator_alerts']['liveness']['enabled'] = True
    monkeypatch.setattr(AppConfigManager, 'get_autotrader_defaults', lambda self: switched_on)

    config = load_autotrader_config(MOCK_PROFILE)

    assert config.operator_alerts.telegram.enabled is False
    assert config.operator_alerts.liveness.enabled is False


def test_a_mock_profile_that_says_so_keeps_its_answer(tmp_path):
    path = _profile_copy(tmp_path, operator_alerts={
        'telegram': {'enabled': True}, 'liveness': {'enabled': True}})

    config = load_autotrader_config(path)

    assert config.operator_alerts.telegram.enabled is True
    assert config.operator_alerts.liveness.enabled is True
