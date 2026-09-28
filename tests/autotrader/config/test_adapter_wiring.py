"""
A real broker adapter is never fed replayed ticks.

Three keys shape an AutoTrader session — `adapter_type`, `tick_source.type` and the presence of
`scenario_settings` — and the loader accepted any mix of them. The documentation even advertised
a live adapter on replayed ticks as a way to "test order execution with replay data". With the
broker's dry_run off, that session decides on history and sends real orders at today's market.
"""

import json

import pytest

from python.configuration.autotrader.autotrader_config_loader import load_autotrader_config
from python.framework.exceptions.live_execution_errors import AdapterWiringError

_LIVE_PROFILE = 'configs/autotrader_profiles/production/dotusd_live.json'
_MOCK_PROFILE = 'configs/autotrader_profiles/mock/mock_session_test.json'


def _write(tmp_path, source: str, name: str, **changes) -> str:
    """
    A copy of a shipped profile with some top-level keys replaced.

    Args:
        tmp_path: pytest's temporary directory
        source: The shipped profile to copy
        name: File name of the copy — unique, so the name-conflict check has nothing to say
        changes: Top-level keys to set

    Returns:
        The copy's path
    """
    with open(source, encoding='utf-8') as handle:
        raw = json.load(handle)
    raw.update(changes)
    path = tmp_path / name
    path.write_text(json.dumps(raw), encoding='utf-8')
    return str(path)


def test_a_live_adapter_on_the_replaying_tick_source_is_refused(tmp_path):
    path = _write(tmp_path, _LIVE_PROFILE, 'wiring_live_on_mock_ticks.json',
                  tick_source={'type': 'mock'})
    with pytest.raises(AdapterWiringError, match="tick_source.type is 'mock'"):
        load_autotrader_config(path)


def test_a_live_adapter_with_a_replayed_data_window_is_refused(tmp_path):
    with open(_MOCK_PROFILE, encoding='utf-8') as handle:
        window = json.load(handle)['scenario_settings']
    path = _write(tmp_path, _LIVE_PROFILE, 'wiring_live_with_window.json',
                  scenario_settings=window)
    with pytest.raises(AdapterWiringError, match='scenario_settings is present'):
        load_autotrader_config(path)


def test_a_mock_adapter_on_a_real_feed_stays_allowed(tmp_path):
    """It places nothing at any venue — the way a feed is watched without trading."""
    path = _write(tmp_path, _LIVE_PROFILE, 'wiring_mock_on_real_feed.json', adapter_type='mock')
    assert load_autotrader_config(path).adapter_type == 'mock'
