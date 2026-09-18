"""
Whether a live session joins a deployment — who may say so, and who may only narrow it (#497).

A deployment is the span of one bot across its restarts. It is DECLARED in the profile and
never inferred, and the command line can only take it away. The asymmetry is the design, and it
comes from one case: an unattended restart re-executes a command nobody typed, so a deployment
declared on the command line would fragment at exactly the restarts it exists to span —
silently, because a missing flag looks like a one-off.

The declaration is MANDATORY for a reason the `dry_run` family does not share. There `True` is
the safe value: forgetting it prevents real orders. Here neither value is safe. Forgotten on a
deployed profile, a month of history is gone and the join key cannot be added afterwards; set
wrongly on a one-off profile, unrelated runs are welded together and every figure is still
readable. With no safe value to inherit, the profile has to say — and a profile that does not
fails at load, before anything runs.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from python.configuration.autotrader.autotrader_config_loader import load_autotrader_config
from python.framework.autotrader.autotrader_main import AutotraderMain
from python.framework.exceptions.live_execution_errors import OneOffInsideDeploymentError
from python.framework.types.autotrader_types.autotrader_config_types import DeploymentConfig
from python.framework.types.persistence_types import ColdStartPayload

PROFILE_ROOT = Path(__file__).resolve().parents[3] / 'configs' / 'autotrader_profiles'
A_LOADABLE_PROFILE = PROFILE_ROOT / 'backtesting' / 'minimal_warmup_test.json'


def write_profile(tmp_path: Path, deployment) -> str:
    """
    Copy a real profile and replace its deployment block.

    Built from a profile that actually loads, so a refusal below can only come from the
    block under test and not from some unrelated key the fixture forgot.

    Args:
        tmp_path: The test's own directory
        deployment: The block to write, or a sentinel string 'omit' to leave it out

    Returns:
        Path to the written profile
    """
    raw = json.loads(A_LOADABLE_PROFILE.read_text())
    if deployment == 'omit':
        raw.pop('deployment', None)
    else:
        raw['deployment'] = deployment
    path = tmp_path / 'profile_under_test.json'
    path.write_text(json.dumps(raw))
    return str(path)


def resolve(continuous: bool, carried: str = '',
            one_off: bool = False, new_deployment: bool = False) -> str:
    """
    Resolve one profile/CLI combination to the identity the session records.

    Exercised through `__new__`: the method reads three attributes, and building a real
    session would drag in a broker, a tick source and a decision logic without testing any
    of them.

    Args:
        continuous: What the profile declares
        carried: The deployment the predecessor left in the carry-over, '' for none
        one_off: The `--one-off` flag
        new_deployment: The `--new-deployment` flag

    Returns:
        The resolved identity, '' for a session that stands alone
    """
    session = AutotraderMain.__new__(AutotraderMain)
    session._config = SimpleNamespace(deployment=DeploymentConfig(continuous=continuous))
    session._carried = ColdStartPayload(deployment_id=carried or None)
    session._one_off = one_off
    session._new_deployment = new_deployment
    return session._resolve_deployment(datetime(2026, 9, 17, 6, 0, tzinfo=timezone.utc))


class TestTheProfileMustSay:
    """The loader refuses what it cannot safely guess."""

    def test_a_profile_without_the_block_is_refused(self, tmp_path):
        with pytest.raises(ValueError) as caught:
            load_autotrader_config(write_profile(tmp_path, 'omit'))
        assert 'deployment' in str(caught.value)

    def test_a_block_without_continuous_is_refused_too(self, tmp_path):
        """
        An empty block is the shape a half-finished copy-paste leaves behind, and it is
        exactly as undeclared as no block at all.
        """
        with pytest.raises(ValueError):
            load_autotrader_config(write_profile(tmp_path, {}))

    def test_the_refusal_says_what_to_write(self, tmp_path):
        """
        An abort that does not say what to do next gets worked around, and here the
        workaround would be whichever value the operator guesses first.
        """
        with pytest.raises(ValueError) as caught:
            load_autotrader_config(write_profile(tmp_path, 'omit'))
        message = str(caught.value)
        assert '"continuous": false' in message
        assert 'no safe default' in message

    @pytest.mark.parametrize('declared', [True, False])
    def test_a_declared_profile_loads_and_keeps_its_answer(self, tmp_path, declared):
        config = load_autotrader_config(write_profile(tmp_path, {'continuous': declared}))
        assert config.deployment.continuous is declared

    def test_every_tracked_profile_declares_it(self):
        """
        The set as it stands, not merely the loader's rule.

        The loadability suite would already fail on an undeclared profile — but it would
        fail as "this file does not parse", which reads like a typo. This says what is
        actually missing, and it is the check that keeps a NEW profile from being written
        without the line.
        """
        undeclared = [
            p.relative_to(PROFILE_ROOT)
            for p in sorted(PROFILE_ROOT.rglob('*.json'))
            if 'continuous' not in json.loads(p.read_text()).get('deployment', {})
        ]
        assert not undeclared, f'profiles that declare no deployment: {undeclared}'


class TestTheCommandLineMayOnlyNarrow:
    """Every combination, because the asymmetry is the whole point."""

    def test_a_one_off_profile_never_joins_anything(self):
        assert resolve(continuous=False) == ''

    def test_no_flag_can_promote_a_one_off_profile(self):
        """
        Declaring a deployment from the command line is deliberately impossible. Were it
        possible, the restart that re-executes a stored command without the flag would end
        the deployment at the exact moment it was needed.
        """
        assert resolve(continuous=False, new_deployment=True) == ''
        assert resolve(continuous=False, carried='deploy_earlier') == ''

    def test_a_continuous_profile_mints_when_nothing_was_carried(self):
        """The first session of a deployment names itself."""
        minted = resolve(continuous=True)
        assert minted
        assert '20260917' in minted

    def test_a_continuous_profile_inherits_what_the_predecessor_left(self):
        assert resolve(continuous=True, carried='deploy_20260901_060000_ab12') \
            == 'deploy_20260901_060000_ab12'

    def test_one_off_is_the_PROBE_before_a_deployment_exists(self):
        """
        The intended order: a probe day first, the continuous start afterwards. Nobody runs
        an algo for thirty days and then leaves — so the flag has to work on a profile that
        declares `continuous` but has never run.
        """
        assert resolve(continuous=True, one_off=True) == ''

    def test_one_off_is_REFUSED_once_the_deployment_exists(self):
        """
        Past that point the same flag means something else: the session still trades the
        account, but leaves no mark on the history its drawdown keeps running inside. Two
        columns of one table would then describe different periods.
        """
        with pytest.raises(OneOffInsideDeploymentError):
            resolve(continuous=True, carried='deploy_20260901_060000_ab12', one_off=True)

    def test_the_refusal_names_all_three_ways_out(self):
        """
        A refusal that does not say what to do instead gets worked around, and here every
        workaround is worse than the thing refused.
        """
        with pytest.raises(OneOffInsideDeploymentError) as caught:
            resolve(continuous=True, carried='deploy_20260901_060000_ab12', one_off=True)
        message = str(caught.value)
        assert 'deploy_20260901_060000_ab12' in message
        assert '--new-deployment' in message
        assert 'copy the profile' in message
        assert 'BEFORE the first continuous start' in message

    def test_new_deployment_begins_a_fresh_one_instead_of_inheriting(self):
        minted = resolve(continuous=True, carried='deploy_20260901_060000_ab12',
                         new_deployment=True)
        assert minted
        assert minted != 'deploy_20260901_060000_ab12'

    def test_one_off_wins_over_new_deployment_while_nothing_is_carried(self):
        """
        Both narrow; the narrower one decides. Standing alone is a stricter answer than
        starting a fresh history, so a command carrying both records no deployment — as long
        as there is no deployment yet to be refused over.
        """
        assert resolve(continuous=True, one_off=True, new_deployment=True) == ''
