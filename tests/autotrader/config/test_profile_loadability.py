"""
Every tracked AutoTrader profile parses.

This exists because they did not. The #438 loader unification moved the account block into
`scenario_settings` and migrated the mock profiles; the **live** ones kept an `account` key the
loader no longer knows, so the structural guard refused all seven — including the field-study
profile, which is a release gate. The failure was invisible for over a week because no test ever
loaded a profile it did not itself construct.

A config file is code that runs at startup and nowhere else. The cheapest possible guard is to load
every one of them, and the reason it is worth having is that the ones that break are the ones nobody
runs daily: live sessions, release gates, one-off observation profiles.
"""

from pathlib import Path

import pytest

from python.configuration.autotrader.autotrader_config_loader import load_autotrader_config

PROFILE_ROOT = Path(__file__).resolve().parents[3] / 'configs' / 'autotrader_profiles'
PROFILES = sorted(PROFILE_ROOT.rglob('*.json'))


def profile_id(path: Path) -> str:
    """Readable test id: the path below configs/autotrader_profiles/."""
    return str(path.relative_to(PROFILE_ROOT))


class TestEveryProfileLoads:
    """The whole tracked set, one test each so a failure names the file."""

    def test_the_set_is_not_empty(self):
        """A glob over a moved directory returns a clean pass and proves nothing."""
        assert len(PROFILES) >= 10

    @pytest.mark.parametrize('path', PROFILES, ids=profile_id)
    def test_profile_parses(self, path):
        config = load_autotrader_config(str(path))
        assert config.name, f'{profile_id(path)} declares no name'
        assert config.symbol, f'{profile_id(path)} declares no symbol'
        assert config.broker_type, f'{profile_id(path)} declares no broker_type'


class TestLiveProfilesDeclareNoBalances:
    """
    Balances for a live session come from the broker, never from the profile.

    Pinned separately because the removed `account` block is the kind of thing that gets
    pasted back in from an older file — and a profile carrying zeroed placeholder balances
    reads as if it configured something.
    """

    @pytest.mark.parametrize(
        'path',
        [p for p in PROFILES if 'backtesting' not in profile_id(p)],
        ids=profile_id,
    )
    def test_no_scenario_settings_on_a_live_profile(self, path):
        config = load_autotrader_config(str(path))
        if config.adapter_type != 'live':
            pytest.skip('mock profile — replays a scenario and carries its balances')
        assert config.scenario_settings is None, (
            f'{profile_id(path)} is a live profile but carries scenario_settings; '
            f'live balances are pulled from the broker'
        )
