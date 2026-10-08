"""
An AutoTrader session records which kind of session it is (contract 12).

Its header says where the ticks come from and where the orders go, resolved from the loaded
configuration by the same rules the session runs by — the replaying tick source, and the one
dry-run rule the broker setup arms the adapter with. A consumer then tells a mock session, a dry
run and a real-money session apart from the run itself, never from a profile file edited since.
"""

from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import patch

from python.configuration.autotrader.autotrader_config_loader import load_autotrader_config
from python.framework.autotrader.autotrader_startup import _data_windows, _ticks_from
from python.framework.autotrader.dry_run_resolver import resolve_orders_to
from python.framework.types.api.report_types import OrdersTo, TicksFrom

_MOCK_PROFILE = 'configs/autotrader_profiles/mock/mock_session_test.json'
_LIVE_PROFILE = 'configs/autotrader_profiles/production/dotusd_production.json'
_START = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
_RESOLVER_MANAGER = 'python.framework.autotrader.dry_run_resolver.MarketConfigManager'


def test_a_mock_session_replays_and_simulates_over_its_declared_window():
    config = load_autotrader_config(_MOCK_PROFILE)

    assert _ticks_from(config) is TicksFrom.ARCHIVE
    assert resolve_orders_to(config) is OrdersTo.SIMULATED
    window = _data_windows(config, _START)[0]
    assert window.start_date.startswith(config.scenario_settings.start_date[:10])
    assert window.unit_name == (config.scenario_settings.scenario_name or config.get_unit_name())


def test_a_dry_run_reads_the_venue_and_simulates_its_orders():
    config = load_autotrader_config(_LIVE_PROFILE)
    with patch(_RESOLVER_MANAGER) as manager:
        manager.return_value.get_dry_run.return_value = True
        assert (_ticks_from(config), resolve_orders_to(config)) == (TicksFrom.VENUE, OrdersTo.SIMULATED)


def test_a_real_money_session_places_its_orders_at_the_venue():
    config = load_autotrader_config(_LIVE_PROFILE)
    with patch(_RESOLVER_MANAGER) as manager:
        manager.return_value.get_dry_run.return_value = False
        assert resolve_orders_to(config) is OrdersTo.VENUE


def test_a_venue_session_is_open_until_it_ends():
    window = _data_windows(load_autotrader_config(_LIVE_PROFILE), _START)[0]
    assert (window.start_date, window.end_date) == (_START.isoformat(), None)


def test_a_profile_the_dry_run_rule_refuses_records_no_destination():
    """The header goes down before the refusal, so the refused session is still identifiable."""
    config = replace(load_autotrader_config(_LIVE_PROFILE), dry_run=False)
    with patch(_RESOLVER_MANAGER) as manager:
        manager.return_value.get_dry_run.return_value = True
        assert resolve_orders_to(config) is None
