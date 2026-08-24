"""
Which setting decides whether a live session places real orders.

This exists because of a near miss: an observation profile carried `dry_run: true`, the broker's
`user_configs/market_config.json` carried `dry_run: false` from an earlier real-money field study,
and the profile field — declared on the config type, documented, and parsed by the loader — was
never read by the resolver. The session would have started cleanly and placed real orders on a
funded account. No crash, no warning, a profile that looked safe and was not.

The rule pinned here is asymmetric on purpose: **a profile may tighten the posture, never loosen
it.** A profile is a per-run file that gets copied and edited; the broker setting is the operator's
standing posture. Enabling real money belongs in the place that is changed deliberately.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from python.framework.autotrader.autotrader_main import AutotraderMain
from python.framework.exceptions.live_execution_errors import DryRunConflictError

BROKER = 'kraken_spot'


def resolve(profile_override, broker_default, adapter_type='live') -> bool:
    """Resolve the effective dry-run flag for one profile/broker combination."""
    session = AutotraderMain.__new__(AutotraderMain)
    session._config = SimpleNamespace(
        name='observation_profile', symbol='BTCUSD', broker_type=BROKER,
        adapter_type=adapter_type, dry_run=profile_override)
    with patch('python.framework.autotrader.autotrader_main.MarketConfigManager') as manager:
        manager.return_value.get_dry_run.return_value = broker_default
        return session._is_dry_run()


class TestBrokerDefaultApplies:
    """A profile that says nothing inherits the broker's posture — today's behaviour."""

    @pytest.mark.parametrize('broker_default', [True, False])
    def test_no_override_inherits(self, broker_default):
        assert resolve(None, broker_default) is broker_default


class TestProfileMayTighten:
    """The direction that is always safe."""

    def test_profile_forces_dry_run_over_a_live_broker(self):
        """
        The near miss, inverted into a guarantee: a profile marked dry_run now *is* a
        dry run, whatever the broker default says.
        """
        assert resolve(True, False) is True

    def test_agreement_is_not_a_conflict(self):
        assert resolve(True, True) is True
        assert resolve(False, False) is False


class TestProfileMayNotLoosen:
    """The direction that costs money."""

    def test_enabling_real_orders_from_a_profile_is_refused(self):
        with pytest.raises(DryRunConflictError) as caught:
            resolve(False, True)
        message = str(caught.value)
        assert 'observation_profile' in message
        assert BROKER in message
        assert 'market_config.json' in message

    def test_the_refusal_names_where_to_change_it(self):
        """
        An abort that does not say what to do next gets worked around, and the workaround
        is usually the unsafe one.
        """
        with pytest.raises(DryRunConflictError) as caught:
            resolve(False, True)
        assert 'tighten' in str(caught.value)


class TestMockIsAlwaysDryRun:
    """No configuration may talk a mock session into placing real orders."""

    @pytest.mark.parametrize('profile_override', [None, True, False])
    def test_mock_ignores_everything(self, profile_override):
        assert resolve(profile_override, False, adapter_type='mock') is True
